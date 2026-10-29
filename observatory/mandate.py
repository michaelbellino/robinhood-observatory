"""Owner-confirmed, versioned research limits. Never an execution authorization.

The gate intentionally stays closed until portfolio coverage and an independent
risk engine can establish that every proposed structure satisfies the mandate.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import Boolean, DateTime, Integer, JSON, String, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Audit, Base, Snapshot, State, aware, now
from .domain import StrictModel
from .rehearsal import fingerprint


class ResearchMandate(Base):
    __tablename__ = "research_mandates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    account_binding: Mapped[str] = mapped_column(String(36), nullable=False)
    account_label: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class MandateInput(StrictModel):
    request_id: UUID
    expected_version: int = Field(ge=0, strict=True)
    confirm_account_last4: str = Field(pattern=r"^[A-Za-z0-9]{4}$")
    universe: Literal["us_listed_equities_etfs", "selected_symbols"]
    symbols: list[str] = Field(max_length=100)
    excluded_symbols: list[str] = Field(max_length=100)
    exclusions: list[str] = Field(max_length=30)
    strategies: list[Literal["defined_risk_credit_spread", "covered_call", "cash_secured_put", "long_call", "long_put"]] = Field(min_length=1, max_length=5)
    proposed_budget_usd: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    max_loss_per_trade_usd: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    max_aggregate_loss_usd: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    max_collateral_usd: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    min_holding_days: int = Field(ge=0, le=365, strict=True)
    max_holding_days: int = Field(ge=1, le=365, strict=True)
    notifications: Literal["material_only", "hourly", "twice_daily", "daily"]
    expires_at: datetime
    acknowledge_recommendations_only: Literal[True]

    @field_validator("symbols", "excluded_symbols")
    @classmethod
    def symbols_are_valid(cls, values):
        import re
        if len(values) != len(set(values)) or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", s) for s in values):
            raise ValueError("Symbols must be unique uppercase US ticker symbols")
        return values

    @model_validator(mode="after")
    def validate_scope(self):
        if self.universe == "selected_symbols" and not self.symbols:
            raise ValueError("Choose at least one symbol")
        if set(self.symbols) & set(self.excluded_symbols):
            raise ValueError("Included and excluded symbols overlap")
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("Strategies must be unique")
        if self.max_loss_per_trade_usd > self.max_aggregate_loss_usd:
            raise ValueError("Per-trade risk must not exceed aggregate risk")
        if self.min_holding_days > self.max_holding_days:
            raise ValueError("Minimum holding period must not exceed the maximum")
        if any(not x.strip() or len(x) > 200 for x in self.exclusions):
            raise ValueError("Exclusions must be nonempty and at most 200 characters")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("Mandate expiration must include a timezone")
        if not now() < self.expires_at.astimezone(timezone.utc) <= now() + timedelta(days=366):
            raise ValueError("Mandate expiration must be within the next year")
        return self


class DisableInput(StrictModel):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)


def _latest(session):
    return session.scalar(select(ResearchMandate).order_by(ResearchMandate.version.desc()).limit(1))


def _show(row, state, *, superseded=False):
    valid_account = bool(state and state.account_verified and state.account_binding and
                         state.account_binding == row.account_binding)
    active = row.enabled and aware(row.expires_at) > now() and valid_account and not superseded
    return {"mode": "superseded" if superseded else "confirmed_pending_validation" if active else "inactive", "version": row.version,
            "id": row.id, "account": row.account_label, "account_verified": valid_account,
            "created_at": aware(row.created_at), "expires_at": aware(row.expires_at),
            "enabled": row.enabled, "limits": row.payload, "execution": "manual_robinhood",
            "recommendations_active": False,
            "blocker": "Live proposal validation and complete portfolio coverage are required."}


def get_current(db):
    with db.session() as session:
        row = _latest(session)
        if row is None:
            return {"mode": "demo", "version": 0, "account": None, "limits": None,
                    "recommendations_active": False, "execution": "manual_robinhood",
                    "blocker": "No confirmed live research mandate."}
        return _show(row, session.get(State, 1))


def save(db, vault, actor, body):
    request = body.model_dump(mode="json", exclude={"confirm_account_last4"})
    with db.session() as session:
        state = session.scalar(select(State).where(State.id == 1).with_for_update())
        if not state or not state.account_verified or not state.account_binding or not state.account_cipher:
            raise HTTPException(409, "Select and verify the account before confirming a mandate")
        try:
            account = vault.decrypt(state.account_cipher)
            if not isinstance(account, str) or not account.endswith(body.confirm_account_last4):
                raise ValueError()
        except Exception:
            raise HTTPException(409, "Account confirmation does not match the selected account") from None
        request_hash = fingerprint({"input": request, "account_binding": state.account_binding, "actor": actor})
        existing = session.get(ResearchMandate, str(body.request_id))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, "Request ID already used with different mandate terms")
            latest = _latest(session)
            return _show(existing, state, superseded=bool(latest and latest.id != existing.id))
        current = _latest(session)
        version = current.version if current else 0
        if body.expected_version != version:
            raise HTTPException(409, "Mandate changed; refresh before confirming")
        payload = body.model_dump(mode="json", exclude={"request_id", "expected_version", "confirm_account_last4", "acknowledge_recommendations_only", "expires_at"})
        row = ResearchMandate(id=str(body.request_id), version=version + 1, request_hash=request_hash,
                              actor=actor, account_binding=state.account_binding,
                              account_label=state.account_label or "Selected account",
                              created_at=now(), expires_at=body.expires_at, enabled=True, payload=payload)
        session.add(row)
        session.add(Audit(id=str(uuid4()), actor=actor, action="research_mandate_confirmed",
                          details={"mandate_id": row.id, "version": row.version}))
        return _show(row, state)


def disable(db, actor, body):
    with db.session() as session:
        state = session.scalar(select(State).where(State.id == 1).with_for_update())
        if not state:
            raise HTTPException(409, "Account state unavailable")
        request_hash = fingerprint({"action": "disable", "input": body.model_dump(mode="json"), "actor": actor})
        existing = session.get(ResearchMandate, str(body.request_id))
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(409, "Request ID already used")
            latest = _latest(session)
            return _show(existing, state, superseded=bool(latest and latest.id != existing.id))
        current = _latest(session)
        if not current or current.version != body.expected_version:
            raise HTTPException(409, "Mandate changed; refresh before disabling")
        row = ResearchMandate(id=str(body.request_id), version=current.version + 1, request_hash=request_hash,
                              actor=actor, account_binding=current.account_binding, account_label=current.account_label,
                              created_at=now(), expires_at=current.expires_at, enabled=False, payload=current.payload)
        session.add(row)
        session.add(Audit(id=str(uuid4()), actor=actor, action="research_mandate_disabled",
                          details={"mandate_id": row.id, "version": row.version}))
        return _show(row, state)


def live_proposal_gate(session, state, candidate=None):
    """Return explicit blockers; no action is allowed while any evidence is unknown."""
    reasons = []
    row = _latest(session)
    if not row or not row.enabled or aware(row.expires_at) <= now():
        reasons.append("confirmed_unexpired_mandate_missing")
    if not state or not state.account_verified or not state.account_binding:
        reasons.append("verified_account_missing")
    elif row and row.account_binding != state.account_binding:
        reasons.append("mandate_account_changed")
    snapshot = session.scalar(select(Snapshot).where(Snapshot.source == "robinhood_mcp_live")
                              .order_by(Snapshot.observed_at.desc()).limit(1))
    if not snapshot or not state or snapshot.account_binding != state.account_binding or snapshot.generation != state.generation:
        reasons.append("account_bound_snapshot_missing")
    else:
        def recent(value):
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
                if not stamp or stamp.tzinfo is None or stamp.utcoffset() is None:
                    return False
                return -5 <= (now() - stamp).total_seconds() <= 300
            except (ValueError, TypeError, OverflowError):
                return False

        if not recent(aware(snapshot.observed_at)):
            reasons.append("snapshot_stale")
        if not recent(snapshot.payload.get("portfolio_as_of")):
            reasons.append("portfolio_source_timestamp_stale_or_unknown")
        feeds = snapshot.payload.get("feed_metadata") or {}
        required = ("portfolio", "equity_positions", "option_positions", "equity_orders", "option_orders",
                    "advanced_orders", "crypto_positions", "crypto_orders")
        if any(not isinstance(feeds.get(name), dict) or feeds[name].get("complete") is not True
               or not recent(feeds[name].get("received_at")) for name in required):
            reasons.append("portfolio_feed_coverage_or_freshness_incomplete")
        if snapshot.payload.get("open_orders_complete") is not True:
            reasons.append("open_order_coverage_incomplete")
    risk = None
    if candidate is None:
        reasons.append("candidate_risk_evaluation_required")
    elif row and snapshot:
        from .risk_validation import evaluate
        risk = evaluate(candidate, row.payload, snapshot.payload, now())
        reasons.extend(risk["reasons"])
    else:
        reasons.append("portfolio_risk_inputs_unavailable")
    return {"allowed": not reasons, "reasons": sorted(set(reasons)), "mandate_id": row.id if row else None,
            "snapshot_id": snapshot.id if snapshot else None, "risk": risk}
