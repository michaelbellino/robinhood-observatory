"""A paper-only review workbench. This module has no broker or model client.

Caps are test constraints, not a real-money mandate. Review records cannot be
used as execution authorizations. Financial fixtures stay separate from live data.
"""
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, select

from .db import Audit, Rehearsal, State, aware, now
from .domain import StrictModel

PER_TICKET = Decimal("5")
PER_DAY = Decimal("20")
DAILY_TICKETS = 10
TTL_SECONDS = 300
LABEL = "SYNTHETIC PAPER TEST — NO BROKERAGE ORDER"


class TestTerms(StrictModel):
    asset: Literal["equity", "option"]
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,11}$")
    side: Literal["buy"] = "buy"
    quantity: Decimal = Field(gt=0, le=10000, max_digits=16, decimal_places=8)
    limit_price: Decimal = Field(gt=0, le=1000000, max_digits=16, decimal_places=8)
    fees_allowance: Decimal = Field(default=Decimal("0"), ge=0, le=5, decimal_places=2)
    time_in_force: Literal["day"] = "day"
    expiration: date | None = None
    strike: Decimal | None = Field(default=None, gt=0, le=1000000, decimal_places=4)
    right: Literal["call", "put"] | None = None
    multiplier: int = Field(default=1, strict=True, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_contract(self):
        if self.asset == "option":
            if self.quantity != 1:
                raise ValueError("Option rehearsal limited to one long contract")
            if not all((self.expiration, self.strike, self.right)):
                raise ValueError("Full option terms required")
            if "multiplier" not in self.model_fields_set:
                raise ValueError("Option multiplier must be explicit")
        elif any((self.expiration, self.strike, self.right)) or self.multiplier != 1:
            raise ValueError("Equity rehearsal cannot include option terms")
        return self


class CreateRehearsal(StrictModel):
    request_id: UUID
    terms: TestTerms


class ReviewRehearsal(StrictModel):
    request_id: UUID
    terms_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: Literal["reviewed", "rejected"]
    acknowledge_paper_only: Literal[True]


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def limits():
    return {
        "mode": "synthetic_paper_test", "real_money_limit_usd": "0",
        "per_ticket_paper_usd": str(PER_TICKET), "daily_reserved_paper_usd": str(PER_DAY),
        "daily_ticket_limit": DAILY_TICKETS, "review_expires_seconds": TTL_SECONDS,
        "allowed": ["equity buy", "one long option contract"],
        "model_calls": 0, "broker_calls": 0, "automatic_fills": False,
        "note": "Test caps are not spending permissions. No brokerage order is created or sent.",
    }


def view(row, at=None):
    state = row.status
    if state == "awaiting_paper_review" and aware(row.expires_at) <= (at or now()):
        state = "expired"
    return {"id": row.id, "created_at": aware(row.created_at).isoformat(),
            "expires_at": aware(row.expires_at).isoformat(), "terms_hash": row.terms_hash,
            "status": state, "payload": row.payload, "fill_status": "unfilled", "sent_to_broker": False}


def recent(db, limit=20):
    with db.session() as session:
        rows = session.scalars(select(Rehearsal).order_by(Rehearsal.created_at.desc()).limit(limit))
        return [view(row) for row in rows]


def create(db, actor, request: CreateRehearsal):
    at = now()
    terms = request.terms
    ident = str(request.request_id)
    request_hash = fingerprint(terms.model_dump(mode="json"))
    # Round upward, never truncate tiny debits to zero in accounting.
    debit = (terms.quantity * terms.limit_price * terms.multiplier + terms.fees_allowance).quantize(
        Decimal("0.00000001"), rounding=ROUND_CEILING
    )
    with db.session() as session:
        # Serializes idempotency and budget reservations across web replicas.
        session.scalar(select(State).where(State.id == 1).with_for_update())
        previous = session.get(Rehearsal, ident)
        if previous:
            if previous.actor != actor or previous.request_hash != request_hash:
                raise HTTPException(409, "Request ID already used with different terms")
            return {**view(previous, at), "replayed": True}
        if debit > PER_TICKET:
            raise HTTPException(422, "Paper test exceeds the $5 per-ticket cap")
        if terms.expiration and terms.expiration <= at.astimezone(ZoneInfo("America/New_York")).date():
            raise HTTPException(422, "Use a future expiration in this paper test")
        day = at.astimezone(ZoneInfo("America/New_York")).replace(hour=0, minute=0, second=0, microsecond=0)
        used, count = session.execute(select(
            func.coalesce(func.sum(Rehearsal.reserved_usd), 0), func.count(Rehearsal.id)
        ).where(Rehearsal.created_at >= day)).one()
        if Decimal(used) + debit > PER_DAY or count >= DAILY_TICKETS:
            raise HTTPException(429, "Daily paper-test cap reached; resets at New York midnight")
        expires = at + timedelta(seconds=TTL_SECONDS)
        payload = {
            "label": LABEL, "schema_version": "paper-review/v1", "source": "synthetic_user_test",
            "terms": terms.model_dump(mode="json"), "paper_debit_usd": str(debit),
            "expires_at": expires.isoformat(), "account": "isolated paper rehearsal",
            "quote": "No live quote used; price is a hypothetical input",
            "limitations": ["No fill, execution support, buying power or profitability established",
                            "Option assignment, exercise and expiry exposure are not simulated",
                            "Review cannot authorize a present or future brokerage order"],
        }
        row = Rehearsal(id=ident, actor=actor, created_at=at, expires_at=expires,
                        request_hash=request_hash, terms_hash=fingerprint(payload), payload=payload,
                        reserved_usd=debit, status="awaiting_paper_review")
        session.add(row)
        session.add(Audit(id=str(uuid4()), actor=actor,
                          action="paper_test_created", details={"rehearsal_id": ident}))
        return {**view(row, at), "replayed": False}


def review(db, actor, ident, request: ReviewRehearsal):
    with db.session() as session:
        session.scalar(select(State).where(State.id == 1).with_for_update())
        row = session.get(Rehearsal, str(ident))
        if row is None:
            raise HTTPException(404, "Paper rehearsal not found")
        if row.terms_hash != request.terms_hash or fingerprint(row.payload) != request.terms_hash:
            raise HTTPException(409, "Terms changed; review the exact current ticket")
        details = {"rehearsal_id": row.id, "terms_hash": request.terms_hash, "decision": request.decision}
        previous = session.get(Audit, str(request.request_id))
        if previous:
            if previous.actor != actor or previous.action != "paper_test_review" or previous.details != details:
                raise HTTPException(409, "Review request ID reused")
            return {**view(row), "replayed": True}
        if aware(row.expires_at) <= now():
            raise HTTPException(409, "Paper review expired; create a new ticket")
        if row.status != "awaiting_paper_review":
            raise HTTPException(409, "Paper ticket already resolved")
        row.status = "paper_reviewed" if request.decision == "reviewed" else "paper_rejected"
        session.add(Audit(id=str(request.request_id), actor=actor, action="paper_test_review", details=details))
        return {**view(row), "replayed": False}


def fixture(asset):
    values = {"asset": asset, "symbol": "TEST", "quantity": "1", "limit_price": "1"}
    if asset == "option":
        values.update(limit_price="0.01", multiplier=100, strike="100", right="call",
                      expiration=(now().astimezone(ZoneInfo("America/New_York")).date() + timedelta(days=30)))
    return TestTerms(**values)
