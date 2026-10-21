"""Real-instrument research inbox and broker previews; never submits an order.

Ideas are not brokerage orders. Review decisions never become execution credentials.
The producer (human or research agent) must supply its methodology and exact terms.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4
from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from .db import TradeIdea, OrderSubmission, State, Snapshot, Event, Audit, aware, now
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
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    dollar_amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    limit_price: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4)
    stop_price: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4)
    side: Literal["buy", "sell"] | None = None
    direction: Literal["debit", "credit"] | None = None
    legs: list[OptionLeg] = Field(default_factory=list, max_length=4)
    underlying_type: Literal["equity", "index"] = "equity"
    type: Literal["limit", "market", "stop_limit", "stop_market"] = "limit"
    market_hours: Literal["regular_hours", "extended_hours", "all_day_hours",
                          "regular_curb_hours", "regular_curb_overnight_hours"] = "regular_hours"
    time_in_force: Literal["gfd", "gtc"] = "gfd"

    @model_validator(mode="after")
    def shape(self):
        if (self.limit_price is not None) != (self.type in {"limit", "stop_limit"}):
            raise ValueError("Limit price is required only for limit and stop-limit orders")
        if (self.stop_price is not None) != self.type.startswith("stop_"):
            raise ValueError("Stop price is required only for stop orders")
        if self.market_hours != "regular_hours" and self.type != "limit":
            raise ValueError("Extended and overnight sessions support limit orders only")
        if self.asset == "equity":
            if self.side is None or self.legs or self.direction is not None:
                raise ValueError("Stock terms require side and no option legs/direction")
            if (self.quantity is None) == (self.dollar_amount is None):
                raise ValueError("Choose shares or a dollar amount, not both")
            if self.market_hours not in {"regular_hours", "extended_hours", "all_day_hours"}:
                raise ValueError("Choose a stock trading session")
            fractional = self.quantity is not None and self.quantity != self.quantity.to_integral_value()
            if (fractional or self.dollar_amount is not None) and (self.type != "market" or self.market_hours != "regular_hours"):
                raise ValueError("The broker connection supports fractional/dollar orders only as regular-hours market orders")
        else:
            if self.quantity is None or self.quantity != self.quantity.to_integral_value() or self.dollar_amount is not None:
                raise ValueError("Options require a whole number of contracts or strategies")
            if not self.legs or self.side is not None:
                raise ValueError("Options require exact legs and no stock side")
            if len({x.option_id for x in self.legs}) != len(self.legs):
                raise ValueError("Duplicate contracts")
            if len(self.legs) > 1 and self.direction is None:
                raise ValueError("Multi-leg net direction required")
            if len(self.legs) == 1 and (self.direction is not None or self.legs[0].ratio_quantity != 1):
                raise ValueError("Single-leg direction is derived from side; ratio must be one")
            if len(self.legs) > 1 and self.type != "limit":
                raise ValueError("The broker supports multi-leg orders as limits only")
            if self.market_hours not in {"regular_hours", "regular_curb_hours", "regular_curb_overnight_hours"}:
                raise ValueError("Choose an options trading session")
            if self.market_hours != "regular_hours" and self.underlying_type != "index":
                raise ValueError("Extended options sessions require an eligible index chain")
            if self.type in {"market", "stop_market"} and self.time_in_force != "gfd":
                raise ValueError("Options market and stop-market orders require good for day")
            if self.type == "stop_market" and (self.legs[0].side, self.legs[0].position_effect) != ("sell", "close"):
                raise ValueError("The broker supports options stop-market orders only for sell to close")
        return self

    def broker_arguments(self, account):
        args = {"account_number": account, "type": self.type,
                "market_hours": self.market_hours, "time_in_force": self.time_in_force}
        if self.quantity is not None:
            args["quantity"] = str(int(self.quantity)) if self.asset == "option" else format(self.quantity, "f")
        if self.stop_price is not None:
            args["stop_price"] = str(self.stop_price)
        if self.asset == "equity":
            args.update(symbol=self.symbol, side=self.side)
            if self.limit_price is not None:
                args["limit_price"] = str(self.limit_price)
            if self.dollar_amount is not None:
                args["dollar_amount"] = str(self.dollar_amount)
            return args
        if self.limit_price is not None:
            args["price"] = str(self.limit_price)
        args.update(chain_symbol=self.symbol, underlying_type=self.underlying_type,
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
    valid_for_seconds: int = Field(default=900, ge=60, le=604800, strict=True)

    @model_validator(mode="after")
    def source_lengths(self):
        if any(not x.strip() or len(x) > 500 for x in self.sources):
            raise ValueError("Sources must be nonempty and bounded")
        return self


class OwnerTicket(StrictModel):
    request_id: UUID
    terms: Terms
    note: str = Field(default="", max_length=2000)


class Revision(StrictModel):
    request_id: UUID
    terms_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    terms: Terms
    valid_for_seconds: int = Field(default=900, ge=60, le=604800, strict=True)


def revise(db, actor, ident, body):
    """An edit retires the old version atomically. It never carries approval forward."""
    with db.session() as s:
        state = s.scalar(select(State).where(State.id == 1).with_for_update())
        row = s.get(TradeIdea, str(ident))
        if not row:
            raise HTTPException(404, "Proposal not found")
        request_hash = fingerprint(body.model_dump(mode="json"))
        old = s.get(TradeIdea, str(body.request_id))
        if old:
            if old.parent_id != row.id or old.request_hash != request_hash:
                raise HTTPException(409, "Revision request ID conflict")
            return serialize(old)
        sub = s.get(OrderSubmission, row.id)
        if (row.status not in {"awaiting_review", "needs_review"} or row.broker_order_id
                or (sub and sub.state != "not_sent") or row.terms_hash != body.terms_hash):
            raise HTTPException(409, "Proposal changed or submission started; refresh its status")
        if not state.account_verified or not state.account_cipher:
            raise HTTPException(409, "Verify the selected account before creating a new version")
        payload = {**row.payload, "terms": body.terms.model_dump(mode="json")}
        new = TradeIdea(id=str(body.request_id), parent_id=row.id, version=(row.version or 1)+1,
            payload=payload, request_hash=request_hash, terms_hash=fingerprint(payload),
            account_cipher=state.account_cipher, account_label=state.account_label,
            created_at=now(), expires_at=now()+timedelta(seconds=body.valid_for_seconds), status="awaiting_review")
        row.status = "superseded"
        s.add(new)
        s.add(Audit(id=str(uuid4()), actor=actor, action="proposal_revised",
                    details={"previous_id": row.id, "idea_id": new.id, "version": new.version}))
        s.add(Event(id=fingerprint({"idea": new.id}), kind="trade_idea", payload={"idea_id": new.id}))
        return serialize(new)


def create_owner_ticket(db, actor, body):
    return create(db, actor, NewIdea(request_id=body.request_id, terms=body.terms,
        rationale=body.note if len(body.note) >= 10 else "Order entered directly by the account owner.",
        risks="Review price, session, fees, and all broker alerts before submitting a real order.",
        methodology="owner-directed", sources=["Owner order entry"], valid_for_seconds=86400))


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
            "account": row.account_label, "account_bound": bool(row.account_cipher),
            "version": row.version or 1, "parent_id": row.parent_id,
            "status": status, "terms_hash": row.terms_hash, "payload": row.payload,
            "preview": row.preview, "preview_at": aware(row.preview_at),
            "preview_hash": fingerprint(row.preview) if row.preview else None,
            "sent_to_broker": (None if row.status in {"submission_in_progress", "submission_unknown"}
                               else row.status == "submitted_by_owner"),
            "execution_route": "owner_dashboard", "broker_order": None}
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
        # Duplicate proposals should not cause repeated alerts during the review window.
        terms_hash = fingerprint(payload)
        duplicate = s.scalar(select(TradeIdea).where(TradeIdea.terms_hash == terms_hash,
                            TradeIdea.account_cipher == state.account_cipher,
                            TradeIdea.status == "awaiting_review", TradeIdea.expires_at > now()))
        if duplicate:
            return serialize(duplicate)
        row = TradeIdea(id=str(body.request_id), payload=payload, request_hash=request_hash,
                        account_cipher=state.account_cipher, account_label=state.account_label, version=1,
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
    size_key = "dollar_amount" if terms.dollar_amount is not None else "quantity"
    if Decimal(str(result.get(size_key))) != getattr(terms, size_key) or result.get("type") != terms.type:
        raise MalformedData()
    price = result.get("limit_price" if terms.asset == "equity" else "price")
    if ((terms.limit_price is not None and (price is None or Decimal(str(price)) != terms.limit_price))
            or (terms.stop_price is not None and Decimal(str(result.get("stop_price"))) != terms.stop_price)
            or (terms.stop_price is None and result.get("stop_price") is not None)
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


def validate_order_echo(terms, order):
    """Normalize the broker's type+trigger representation, then compare exact intent."""
    expected_trigger = "stop" if terms.type.startswith("stop_") else "immediate"
    expected_type = terms.type.removeprefix("stop_")
    if (order.get("trigger", "immediate") != expected_trigger or order.get("type") != expected_type
            or order.get("market_hours") != terms.market_hours or order.get("time_in_force") != terms.time_in_force):
        raise MalformedData()
    echoed = {**order, "type": terms.type}
    if terms.asset == "equity":
        echoed["limit_price"] = order.get("price")
        if terms.dollar_amount is not None:
            amount = order.get("dollar_based_amount") or {}
            if amount.get("currency_code") != "USD":
                raise MalformedData()
            echoed["dollar_amount"] = amount.get("amount")
    validate_echo(terms, echoed, require_checks=False)
    if terms.asset == "option" and order.get("chain_symbol") != terms.symbol:
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
        state = s.scalar(select(State).where(State.id == 1).with_for_update())
        row = s.scalar(select(TradeIdea).where(TradeIdea.id == ident).with_for_update())
        if not row:
            raise HTTPException(404, "Idea not found")
        submission = s.get(OrderSubmission, ident)
        if submission and submission.state != "not_sent":
            raise HTTPException(409, "Submission already started; check its result before further action")
        if not state.account_verified or row.status != "awaiting_review" or aware(row.expires_at) <= now():
            raise HTTPException(409, "Idea expired, reviewed, or brokerage connection unavailable")
        if not row.account_cipher or row.account_cipher != state.account_cipher:
            raise HTTPException(409, "Selected account changed; create and review a new proposal version")
        if row.preview_started_at and aware(row.preview_started_at) > now()-timedelta(seconds=180):
            raise HTTPException(429, "Preview already in progress; wait before retrying")
        if (row.preview_at and aware(row.preview_at) > now()-timedelta(seconds=30)
                and row.preview_generation == state.generation):
            return serialize(row)
        started_at = now()
        row.preview_started_at = started_at
        account, terms_hash, generation = vault.decrypt(state.account_cipher), row.terms_hash, state.generation
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
        # Review responses need not echo session/TIF. Bind their exact request locally.
        result["requested_terms"] = terms.model_dump(mode="json")
        with db.session() as s:
            state = s.scalar(select(State).where(State.id == 1).with_for_update())
            row = s.scalar(select(TradeIdea).where(TradeIdea.id == ident).with_for_update())
            if (row.status != "awaiting_review" or row.terms_hash != terms_hash or aware(row.expires_at) <= now()
                    or aware(row.preview_started_at) != started_at or state.generation != generation
                    or not state.account_verified or vault.decrypt(state.account_cipher) != account):
                raise HTTPException(409, "Idea changed or expired while retrieving preview")
            row.preview, row.preview_at, row.preview_started_at = mask_accounts(result), now(), None
            row.preview_generation = generation
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
        submission = s.get(OrderSubmission, ident)
        if submission and submission.state != "not_sent":
            raise HTTPException(409, "Submission already started; decisions cannot cancel a brokerage order")
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
            if (row.payload["terms"]["type"] in {"market", "stop_market"}
                    and not fresh_preview(row.preview, row.payload["terms"]["asset"])):
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
            if not match:
                raise ValueError()
            validate_order_echo(terms, match)
            if not aware(row.created_at) <= datetime.fromisoformat(match["created_at"].replace("Z", "+00:00")) <= aware(row.expires_at):
                raise ValueError()
        except (ValueError, TypeError, KeyError, ArithmeticError, MalformedData):
            raise HTTPException(409, "Broker order not observed or exact terms do not match") from None
        if row.broker_order_id and row.broker_order_id != str(order_id):
            raise HTTPException(409, "An order is already linked")
        row.broker_order_id = str(order_id)
        s.add(Audit(id=str(uuid4()), actor=actor, action="broker_order_linked", details={"idea_id": str(ident), "order_id": str(order_id)}))
        return serialize(row, snap)

