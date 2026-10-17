from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Contract(StrictModel):
    id: str
    underlying: str
    expiration: date
    strike: Decimal
    right: Literal["call", "put"]
    multiplier: Decimal | None = Field(default=None, gt=0)
    deliverable: str | None = None
    exercise_style: Literal["american", "european"] | None = None
    settlement: Literal["physical", "cash"] | None = None
    settle_on_open: bool | None = None
    sellout_at: AwareDatetime | None = None
    calendar: str | None = None
    source: str = "robinhood_mcp"
    observed_at: AwareDatetime


class Quote(StrictModel):
    instrument_id: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    mark: Decimal | None = None
    bid_at: AwareDatetime | None = None
    ask_at: AwareDatetime | None = None
    quoted_at: AwareDatetime | None = None
    received_at: AwareDatetime
    volume: int | None = None
    open_interest: int | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
    rho: Decimal | None = None
    implied_volatility: Decimal | None = None
    source: str = "robinhood_mcp"

    def freshness(self, at, max_age=300):
        if self.quoted_at is None:
            return "unknown"
        age = (at - self.quoted_at).total_seconds()
        return "fresh" if -5 <= age <= max_age else "stale"

    def book_freshness(self, at, max_age=60):
        if self.bid_at is None or self.ask_at is None or self.bid is None or self.ask is None:
            return "unknown"
        ages = [(at - stamp).total_seconds() for stamp in (self.bid_at, self.ask_at)]
        if self.bid <= 0 or self.ask <= 0 or self.bid > self.ask:
            return "invalid"
        return "fresh" if min(ages) >= -5 and max(ages) <= max_age else "stale"


class Leg(StrictModel):
    contract: Contract
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=10000)
    position_effect: Literal["open", "close"]


class Paper(StrictModel):
    label: Literal["PAPER — NOT A BROKERAGE ORDER"] = "PAPER — NOT A BROKERAGE ORDER"
    structure: Literal[
        "single", "vertical", "calendar", "iron_condor", "roll", "custom", "stock_option_combo"
    ]
    legs: list[Leg] = Field(min_length=1, max_length=8)
    limit_price: Decimal | None = None
    fees_estimate: Decimal | None = Field(default=None, ge=0)
    slippage_per_contract: Decimal | None = Field(default=None, ge=0)
    buying_power_impact: Decimal | None = None
    uncertainty: str = Field(min_length=1, max_length=1200)
    sources: list[str] = Field(min_length=1, max_length=20)
    strategy_version: str = "observe-only/v1"
    broker_support: Literal["unverified", "unsupported"] = "unverified"
    fill_status: Literal["unfilled"] = "unfilled"

    @model_validator(mode="after")
    def structure_rules(self):
        if self.structure == "single" and len(self.legs) != 1:
            raise ValueError("Single-leg structure requires one leg")
        if self.structure in {"custom", "stock_option_combo"}:
            self.broker_support = "unsupported"
        if self.structure == "vertical":
            if len(self.legs) != 2:
                raise ValueError("Vertical requires two legs")
            a, b = self.legs
            if (
                (a.contract.underlying, a.contract.expiration, a.contract.right, a.quantity)
                != (b.contract.underlying, b.contract.expiration, b.contract.right, b.quantity)
                or a.side == b.side
                or a.contract.strike == b.contract.strike
            ):
                raise ValueError("Invalid vertical structure")
        if self.structure == "calendar":
            if len(self.legs) != 2:
                raise ValueError("Calendar requires two legs")
            a, b = self.legs
            if (
                (a.contract.underlying, a.contract.strike, a.contract.right, a.quantity)
                != (b.contract.underlying, b.contract.strike, b.contract.right, b.quantity)
                or a.side == b.side
                or a.contract.expiration == b.contract.expiration
            ):
                raise ValueError("Invalid calendar structure")
        if self.structure == "iron_condor":
            if len(self.legs) != 4 or {x.contract.right for x in self.legs} != {"call", "put"}:
                raise ValueError("Iron condor requires two puts and two calls")
            for right in ["call", "put"]:
                pair = [x for x in self.legs if x.contract.right == right]
                if len(pair) != 2 or pair[0].side == pair[1].side:
                    raise ValueError("Invalid iron condor legs")
            if len({(x.contract.underlying, x.contract.expiration, x.quantity) for x in self.legs}) != 1:
                raise ValueError("Inconsistent iron condor")
            puts = sorted((x for x in self.legs if x.contract.right == "put"), key=lambda x: x.contract.strike)
            calls = sorted((x for x in self.legs if x.contract.right == "call"), key=lambda x: x.contract.strike)
            ordered = puts + calls
            if not all(a.contract.strike < b.contract.strike for a, b in zip(ordered, ordered[1:])):
                raise ValueError("Iron condor strikes must be distinct and ordered")
            if puts[0].side != calls[1].side or puts[1].side != calls[0].side:
                raise ValueError("Iron condor wings must have consistent orientation")
        if self.structure == "roll" and not {"open", "close"}.issubset(
            {x.position_effect for x in self.legs}
        ):
            raise ValueError("Roll requires an opening and closing leg")
        return self


def paper_cost_range(paper: Paper, quotes: dict[str, Quote], at: datetime):
    """Executable-side estimate, never a fill; missing inputs keep cost unknown."""
    total = Decimal(0)
    for leg in paper.legs:
        quote = quotes.get(leg.contract.id)
        if quote is None or quote.book_freshness(at) != "fresh" or leg.contract.multiplier is None:
            return {"estimated_net_debit": None, "fill_status": "unfilled", "reason": "Unknown/stale inputs"}
        price = quote.ask if leg.side == "buy" else quote.bid
        if price is None or quote.bid is None or quote.ask is None or quote.bid > quote.ask:
            return {
                "estimated_net_debit": None,
                "fill_status": "unfilled",
                "reason": "Incomplete/crossed market",
            }
        total += price * leg.quantity * leg.contract.multiplier * (1 if leg.side == "buy" else -1)
    if paper.fees_estimate is None or paper.slippage_per_contract is None:
        return {"estimated_net_debit": None, "fill_status": "unfilled", "reason": "Fees/slippage unknown"}
    total += paper.fees_estimate + paper.slippage_per_contract * sum(x.quantity for x in paper.legs)
    return {
        "estimated_net_debit": str(total),
        "fill_status": "unfilled",
        "reason": "Calculated quote-side estimate",
    }


class Summary(StrictModel):
    summary: str = Field(max_length=2400)
    observations: list[str] = Field(max_length=10)
    uncertainties: list[str] = Field(max_length=10)
    source_refs: list[str] = Field(max_length=20)
    mode: Literal["observe_only"]


class Strategy(Protocol):
    version: str

    def proposals(self, snapshot: dict) -> list[Paper]: ...


class ObserveOnly:
    version = "observe-only/v1"

    def proposals(self, snapshot):
        return []


def decimal_or_unknown(value, positive=False):
    if value is None:
        return None
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Nonfinite financial data")
    return None if positive and number <= 0 else number


def quote_from_broker(row, received_at, options=False):
    q = row.get("quote")
    if not isinstance(q, dict):
        raise ValueError("Missing quote")
    data = {
        "instrument_id": q["instrument_id"] if options else q["symbol"],
        "bid": decimal_or_unknown(q.get("bid_price"), True),
        "ask": decimal_or_unknown(q.get("ask_price"), True),
        "received_at": received_at,
    }
    if options:
        data.update(mark=decimal_or_unknown(q.get("mark_price")), quoted_at=q.get("updated_at"))
        for key in ["delta", "gamma", "theta", "vega", "rho", "implied_volatility"]:
            data[key] = decimal_or_unknown(q.get(key))
        for key in ["volume", "open_interest", "bid_size", "ask_size"]:
            data[key] = q.get(key)
        data["bid_at"] = data["ask_at"] = q.get("updated_at")
    else:
        times = [
            (q.get("venue_last_trade_time"), q.get("last_trade_price")),
            (q.get("venue_last_non_reg_trade_time"), q.get("last_non_reg_trade_price")),
        ]
        times = [(datetime.fromisoformat(t.replace("Z", "+00:00")), p) for t, p in times if t and p]
        if times and q.get("has_traded") is True:
            timestamp, price = max(times)
            data.update(mark=decimal_or_unknown(price), quoted_at=timestamp)
        data.update(bid_at=q.get("venue_bid_time"), ask_at=q.get("venue_ask_time"))
    return Quote(**data)


def contract_from_broker(item, at):
    return Contract(
        id=item["id"],
        underlying=item["chain_symbol"],
        expiration=item["expiration_date"],
        strike=item["strike_price"],
        right=item["type"],
        multiplier=decimal_or_unknown(item.get("trade_value_multiplier"), True),
        sellout_at=item.get("sellout_datetime"),
        observed_at=at,
    )
