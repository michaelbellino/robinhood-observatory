"""Real-instrument research inbox and broker previews; final submission is in Robinhood.

Ideas are not brokerage orders. Review decisions never become execution credentials.
The producer (human or research agent) must supply its methodology and exact terms.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4
from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select, func
from .db import TradeIdea, State, Snapshot, Event, Audit, aware, now
from .domain import StrictModel
from .rehearsal import fingerprint
from .broker import PreviewBroker, MalformedData
from .oauth import RobinhoodOAuth
from .rate_limit import RequestLimiter


class OptionLeg(StrictModel):
    option_id: UUID
    expiration: date
    strike: Decimal = Field(gt=0, le=1000000, decimal_places=4)
    right: Literal["call", "put"]
    side: Literal["buy", "sell"]
    position_effect: Literal["open", "close"]
    ratio_quantity: int = Field(default=1, ge=1, le=100, strict=True)
    multiplier: int = Field(ge=1, le=1000, strict=True)


class Terms(StrictModel):
    asset: Literal["equity", "option"]
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,11}$")
    quantity: int = Field(ge=1, le=10000, strict=True)
    limit_price: Decimal = Field(gt=0, le=1000000, max_digits=14, decimal_places=4)
    side: Literal["buy", "sell"] | None = None
    direction: Literal["debit", "credit"] | None = None
    legs: list[OptionLeg] = Field(default_factory=list, max_length=4)
    underlying_type: Literal["equity", "index"] = "equity"
    type: Literal["limit"] = "limit"
    market_hours: Literal["regular_hours"] = "regular_hours"
    time_in_force: Literal["gfd"] = "gfd"

    @model_validator(mode="after")
    def shape(self):
        if self.asset == "equity":
            if self.side is None or self.legs or self.direction is not None:
                raise ValueError("Stock terms require side and no option legs/direction")
        else:
            if not self.legs or self.side is not None:
                raise ValueError("Options require exact legs and no stock side")
            if len({x.option_id for x in self.legs}) != len(self.legs):
                raise ValueError("Duplicate contracts")
            if len(self.legs) > 1 and self.direction is None:
                raise ValueError("Multi-leg net direction required")
            if len(self.legs) == 1 and (self.direction is not None or self.legs[0].ratio_quantity != 1):
                raise ValueError("Single-leg direction is derived from side; ratio must be one")
        return self

    def broker_arguments(self, account):
        args = {"account_number": account, "quantity": str(self.quantity), "type": self.type,
                "market_hours": self.market_hours, "time_in_force": self.time_in_force}
        if self.asset == "equity":
            return args | {"symbol": self.symbol, "side": self.side, "limit_price": str(self.limit_price)}
        args.update(price=str(self.limit_price), chain_symbol=self.symbol, underlying_type=self.underlying_type,
                    legs=[{k: v for k, v in leg.model_dump(mode="json").items()
                           if k in {"option_id", "side", "position_effect", "ratio_quantity"}} for leg in self.legs])
        if self.direction:
            args["direction"] = self.direction
        return args


class NewIdea(StrictModel):
    request_id: UUID
    terms: Terms
    rationale: str = Field(min_length=10, max_length=2000)
    risks: str = Field(min_length=10, max_length=2000)
    methodology: str = Field(min_length=3, max_length=120)
    sources: list[str] = Field(min_length=1, max_length=8)
    valid_for_seconds: int = Field(default=900, ge=60, le=1800, strict=True)

    @model_validator(mode="after")
    def source_lengths(self):
        if any(not x.strip() or len(x) > 500 for x in self.sources):
            raise ValueError("Sources must be nonempty and bounded")
        return self


class Decision(StrictModel):
    request_id: UUID
    terms_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    decision: Literal["prepare_in_robinhood", "reject"]
    acknowledge_manual_submission: Literal[True]


def serialize(row, snapshot=None):
    status = row.status
    if status in {"awaiting_review", "ready_for_manual_submission"} and not row.broker_order_id and aware(row.expires_at) <= now():
        status = "expired"
    data = {"id": row.id, "created_at": aware(row.created_at), "expires_at": aware(row.expires_at),
            "status": status, "terms_hash": row.terms_hash, "payload": row.payload,
            "preview": row.preview, "preview_at": aware(row.preview_at),
            "preview_hash": fingerprint(row.preview) if row.preview else None,
            "sent_to_broker": False, "execution_route": "manual_robinhood", "broker_order": None}
    if row.broker_order_id and snapshot:
        orders = snapshot.payload.get(f'{row.payload["terms"]["asset"]}_orders', [])
        order = next((x for x in orders if x.get("id") == row.broker_order_id), None)
        if order:
            data["broker_order"] = {"id": order["id"], "state": order["state"],
                                    "filled_quantity": order.get("processed_quantity", order.get("cumulative_quantity")),
                                    "observed_at": aware(snapshot.observed_at)}
    return data


def list_ideas(db, limit=30):
    with db.session() as s:
        snapshot = s.scalar(select(Snapshot).where(Snapshot.source == "robinhood_mcp_live")
                            .order_by(Snapshot.observed_at.desc()))
        return [serialize(x, snapshot) for x in s.scalars(select(TradeIdea).order_by(TradeIdea.created_at.desc())
                                                        .limit(min(max(limit, 1), 100)))]


def create(db, actor, body):
    payload = body.model_dump(mode="json", exclude={"request_id", "valid_for_seconds"})
    request_hash = fingerprint(body.model_dump(mode="json"))
    with db.session() as s:
        state = s.scalar(select(State).where(State.id == 1).with_for_update())
        old = s.get(TradeIdea, str(body.request_id))
        if old:
            if old.request_hash != request_hash:
                raise HTTPException(409, "Request ID already used with different terms")
            return serialize(old)
        if not state.account_verified:
            raise HTTPException(409, "Connect and verify the selected account first")
        count = s.scalar(select(func.count()).select_from(TradeIdea).where(TradeIdea.created_at > now()-timedelta(days=1)))
        if count >= 30:
            raise HTTPException(429, "Research inbox capped at 30 new ideas per 24 hours")
        # Duplicate proposals should not cause repeated alerts during the review window.
        terms_hash = fingerprint(payload)
        duplicate = s.scalar(select(TradeIdea).where(TradeIdea.terms_hash == terms_hash,
                            TradeIdea.status == "awaiting_review", TradeIdea.expires_at > now()))
        if duplicate:
            return serialize(duplicate)
        row = TradeIdea(id=str(body.request_id), payload=payload, request_hash=request_hash,
                        terms_hash=terms_hash, created_at=now(), expires_at=now()+timedelta(seconds=body.valid_for_seconds),
                        status="awaiting_review")
        s.add(row)
        s.add(Event(id=fingerprint({"idea": row.id}), kind="trade_idea", payload={"idea_id": row.id}))
        s.add(Audit(id=str(uuid4()), actor=actor, action="trade_idea_created", details={"idea_id": row.id}))
        return serialize(row)


def mask_accounts(value):
    if isinstance(value, dict):
        return {k: ("••••" + str(v)[-4:] if "account_number" in k else mask_accounts(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_accounts(v) for v in value]
    return value


def validate_echo(terms, result, require_checks=True):
    if Decimal(str(result.get("quantity"))) != terms.quantity or result.get("type") != terms.type:
        raise MalformedData()
    price = result.get("limit_price" if terms.asset == "equity" else "price")
    if (price is None or Decimal(str(price)) != terms.limit_price
            or (require_checks and not isinstance(result.get("order_checks"), dict))):
        raise MalformedData()
    if terms.asset == "equity":
        if result.get("symbol") != terms.symbol or result.get("side") != terms.side:
            raise MalformedData()
        if require_checks and (result.get("quote_data") or {}).get("symbol") != terms.symbol:
            raise MalformedData()
    else:
        expected = terms.broker_arguments("")["legs"]
        actual = result.get("legs") or []
        actual = [{"option_id": x.get("option_id"), "side": x.get("side"), "position_effect": x.get("position_effect"),
                   "ratio_quantity": x.get("ratio_quantity", 1)} for x in actual]
        if sorted(actual, key=lambda x: str(x["option_id"])) != sorted(expected, key=lambda x: x["option_id"]):
            raise MalformedData()
        direction = terms.direction or ("debit" if terms.legs[0].side == "buy" else "credit")
        if result.get("direction") != direction:
            raise MalformedData()
        if require_checks:
            quotes = result.get("option_quotes") or []
            if (len(quotes) != len(terms.legs) or any(not isinstance(x, dict) for x in quotes)
                    or {x.get("instrument_id") for x in quotes} != {str(x.option_id) for x in terms.legs}):
                raise MalformedData()


def fresh_preview(result, asset):
    quotes = [result.get("quote_data")] if asset == "equity" else result.get("option_quotes") or []
    if not quotes:
        return False
    try:
        for quote in quotes:
            bid, ask = Decimal(quote["bid_price"]), Decimal(quote["ask_price"])
            if not (0 < bid <= ask and bid.is_finite() and ask.is_finite()):
                return False
            timestamps = [quote["venue_bid_time"], quote["venue_ask_time"]] if asset == "equity" else [quote["updated_at"]]
            for timestamp in timestamps:
                at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if at.tzinfo is None or not -5 <= (now()-at).total_seconds() <= 60:
                    return False
        return True
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False


async def preview(db, settings, vault, ident, actor, broker_factory=PreviewBroker):
    ident = str(ident)
    with db.session() as s:
        state = s.get(State, 1)
        row = s.scalar(select(TradeIdea).where(TradeIdea.id == ident).with_for_update())
        if not row:
            raise HTTPException(404, "Idea not found")
        if not state.account_verified or row.status != "awaiting_review" or aware(row.expires_at) <= now():
            raise HTTPException(409, "Idea expired, reviewed, or brokerage connection unavailable")
        if row.preview_started_at and aware(row.preview_started_at) > now()-timedelta(seconds=180):
            raise HTTPException(429, "Preview already in progress; wait before retrying")
        if row.preview_at and aware(row.preview_at) > now()-timedelta(seconds=30):
            return serialize(row)
        started_at = now()
        row.preview_started_at = started_at
        account, terms_hash = vault.decrypt(state.account_cipher), row.terms_hash
        terms = Terms.model_validate(row.payload["terms"])
    broker = None
    try:
        broker = broker_factory(await RobinhoodOAuth(db, vault, settings).access_token(), account, settings.broker_rpm)
        broker.limiter = RequestLimiter(db, settings.broker_rpm)
        await broker.initialize()
        if terms.asset == "option":
            instruments = await broker.pages("get_option_instruments", "instruments",
                                             {"ids": ",".join(str(x.option_id) for x in terms.legs)})
            by_id = {x["id"]: x for x in instruments}
            for leg in terms.legs:
                instrument = by_id.get(str(leg.option_id), {})
                if (instrument.get("chain_symbol") != terms.symbol
                        or instrument.get("expiration_date") != leg.expiration.isoformat()
                        or instrument.get("type") != leg.right
                        or instrument.get("underlying_type") != terms.underlying_type
                        or instrument.get("state") != "active" or instrument.get("tradability") != "tradable"
                        or Decimal(str(instrument.get("strike_price", "NaN"))) != leg.strike
                        or Decimal(str(instrument.get("trade_value_multiplier", "NaN"))) != leg.multiplier):
                    raise HTTPException(409, "Option contract identity or multiplier does not match broker metadata")
        result = await broker.call(f"review_{terms.asset}_order", terms.broker_arguments(account))
        validate_echo(terms, result)
        with db.session() as s:
            row = s.scalar(select(TradeIdea).where(TradeIdea.id == ident).with_for_update())
            if (row.status != "awaiting_review" or row.terms_hash != terms_hash or aware(row.expires_at) <= now()
                    or aware(row.preview_started_at) != started_at):
                raise HTTPException(409, "Idea changed or expired while retrieving preview")
            row.preview, row.preview_at, row.preview_started_at = mask_accounts(result), now(), None
            s.add(Audit(id=str(uuid4()), actor=actor, action="broker_preview_read", details={"idea_id": ident}))
            return serialize(row)
    finally:
        if broker:
            await broker.close()
        with db.session() as s:
            row = s.get(TradeIdea, ident)
            if row and aware(row.preview_started_at) == started_at:
                row.preview_started_at = None


def decide(db, actor, ident, body):
    ident = str(ident)
    with db.session() as s:
        s.scalar(select(State).where(State.id == 1).with_for_update())
        row = s.get(TradeIdea, ident)
        if not row:
            raise HTTPException(404, "Idea not found")
        audit = s.get(Audit, str(body.request_id))
        details = {"idea_id": ident, "request": body.model_dump(mode="json")}
        if audit:
            if audit.actor != actor or audit.details != details or audit.action != "trade_idea_decision":
                raise HTTPException(409, "Decision request ID conflict")
            return serialize(row)
        if row.terms_hash != body.terms_hash or fingerprint(row.payload) != row.terms_hash:
            raise HTTPException(409, "Terms changed; review again")
        if row.status != "awaiting_review" or aware(row.expires_at) <= now():
            raise HTTPException(409, "Idea no longer awaiting review")
        if body.decision == "prepare_in_robinhood":
            if (not row.preview or not row.preview_at or aware(row.preview_at) < now()-timedelta(seconds=60)
                    or body.preview_hash != fingerprint(row.preview)):
                raise HTTPException(409, "Refresh the broker preview and review its current terms first")
            if not fresh_preview(row.preview, row.payload["terms"]["asset"]):
                raise HTTPException(409, "Broker quote is stale or unavailable; refresh during the applicable session")
            row.status = "ready_for_manual_submission"
        else:
            row.status = "rejected"
        s.add(Audit(id=str(body.request_id), actor=actor, action="trade_idea_decision", details=details))
        return serialize(row)


def link_order(db, actor, ident, order_id):
    """Link only an observed, matching broker order; a client claim is never fill evidence."""
    with db.session() as s:
        row = s.scalar(select(TradeIdea).where(TradeIdea.id == str(ident)).with_for_update())
        snap = s.scalar(select(Snapshot).where(Snapshot.source == "robinhood_mcp_live").order_by(Snapshot.observed_at.desc()))
        if not row or row.status != "ready_for_manual_submission" or not snap:
            raise HTTPException(409, "Reviewed idea and observed broker order required")
        terms = Terms.model_validate(row.payload["terms"])
        orders = snap.payload.get(f"{terms.asset}_orders", [])
        match = next((x for x in orders if x.get("id") == str(order_id)), None)
        try:
            if not match or Decimal(str(match.get("quantity"))) != terms.quantity or Decimal(str(match.get("price"))) != terms.limit_price:
                raise ValueError()
            if match.get("type") != "limit" or match.get("trigger", "immediate") != "immediate":
                raise ValueError()
            if match.get("time_in_force") != "gfd" or match.get("market_hours") != "regular_hours":
                raise ValueError()
            if not aware(row.created_at) <= datetime.fromisoformat(match["created_at"].replace("Z", "+00:00")) <= aware(row.expires_at):
                raise ValueError()
            if terms.asset == "equity":
                if match.get("symbol") != terms.symbol or match.get("side") != terms.side:
                    raise ValueError()
            else:
                validate_echo(terms, match, require_checks=False)
                if match.get("chain_symbol") != terms.symbol:
                    raise ValueError()
        except (ValueError, TypeError, KeyError, ArithmeticError, MalformedData):
            raise HTTPException(409, "Broker order not observed or exact terms do not match") from None
        if row.broker_order_id and row.broker_order_id != str(order_id):
            raise HTTPException(409, "An order is already linked")
        row.broker_order_id = str(order_id)
        s.add(Audit(id=str(uuid4()), actor=actor, action="broker_order_linked", details={"idea_id": str(ident), "order_id": str(order_id)}))
        return serialize(row, snap)
