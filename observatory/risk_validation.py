"""Deterministic eligibility and payoff checks; never broker authority or execution.

Only call with the server's account-provenanced snapshot, never a request-supplied
portfolio. Optional normalized cash/exit/listing facts require an independently
verified collector; missing facts block. Buying power is never assignment cash.

Payoff references (fees/slippage added separately):
https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write
https://www.optionseducation.org/strategies/all-strategies/cash-secured-put
https://robinhood.com/us/en/support/articles/options-collateral/
https://robinhood.com/us/en/support/articles/expiration-exercise-and-assignment/
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .domain import Contract, Quote
from .research import loss_envelope

TERMINAL = {"filled", "cancelled", "canceled", "rejected", "failed", "voided", "expired",
            "partially_filled_rest_cancelled"}
STRATEGIES = {"credit_spread": "defined_risk_credit_spread", "covered_call": "covered_call",
              "cash_secured_put": "cash_secured_put", "long_call": "long_call", "long_put": "long_put"}
REQUIRED_FEEDS = ("portfolio", "equity_positions", "option_positions", "equity_orders", "option_orders",
                  "advanced_orders", "crypto_positions", "crypto_orders")


def amount(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Unknown or negative monetary amount")
    return result


def recent(value, at, seconds=300):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return bool(stamp and stamp.tzinfo and -5 <= (at-stamp).total_seconds() <= seconds)
    except (ValueError, TypeError, OverflowError):
        return False


def _quotes(rows, at):
    result = {}
    for value in rows:
        quote = Quote.model_validate(value)
        if quote.instrument_id in result or quote.book_freshness(at) != "fresh":
            raise ValueError("Duplicate or stale quote")
        result[quote.instrument_id] = quote
    return result


def _existing_exposure(payload, at):
    """Conservative remaining-downside bound, without crediting unfilled exits.

    Existing short/structured option risk and active option orders need a separate
    reconciled structure model, so remain unsupported rather than being netted.
    Stock orders reserve remaining quantity; already-filled quantity is in holdings.
    """
    if any(not isinstance(payload.get(key), list) for key in ("equity_positions", "option_positions", "equity_orders", "option_orders", "advanced_orders", "crypto_positions", "crypto_orders")):
        raise ValueError("Unknown portfolio feed")
    for key in ("futures_value", "event_contracts_value", "fixed_income_value", "mutual_funds_value"):
        if amount(payload["portfolio"][key]) != 0:
            raise ValueError("Unsupported additional portfolio asset")
    equity_quotes = _quotes(payload["quotes"], at)
    option_quotes = _quotes(payload["option_quotes"], at)
    specs = {x["id"]: Contract.model_validate(x) for x in payload["contracts"]}
    shares, reserved, total = {}, {}, Decimal(0)
    for position in payload["equity_positions"]:
        symbol, quantity = position["symbol"], amount(position["quantity"])
        if position.get("type", "long") != "long" or symbol in shares:
            raise ValueError("Short or duplicate equity position")
        shares[symbol] = quantity
        # Use ask as a conservative current downside bound, not cost basis.
        total += equity_quotes[symbol].ask * quantity
    for position in payload["option_positions"]:
        quantity = amount(position["quantity"])
        if position.get("type") != "long" or quantity != quantity.to_integral_value():
            raise ValueError("Existing short or unrecognized option position")
        spec, quote = specs[position["option_id"]], option_quotes[position["option_id"]]
        if spec.multiplier != 100 or spec.deliverable != "100 shares":
            raise ValueError("Nonstandard existing deliverable")
        if any(amount(position.get(key, 0)) for key in ("pending_assignment_quantity", "pending_exercise_quantity", "pending_expiration_quantity")):
            raise ValueError("Pending option event")
        total += quote.ask * quantity * spec.multiplier
    for order in payload["equity_orders"]:
        if order.get("state") in TERMINAL:
            continue
        remaining = amount(order["quantity"]) - amount(order["cumulative_quantity"])
        if remaining < 0:
            raise ValueError("Invalid remaining order quantity")
        symbol = order["symbol"]
        if order["side"] == "sell":
            reserved[symbol] = reserved.get(symbol, Decimal(0)) + remaining
        elif order["side"] == "buy" and order["type"] == "limit":
            total += amount(order["price"]) * remaining
        else:
            raise ValueError("Unbounded or unrecognized pending equity order")
    if any(x.get("state") not in TERMINAL for x in payload["option_orders"]):
        raise ValueError("Working option orders require reconciled structure reservations")
    if payload["advanced_orders"] or payload["crypto_positions"] or payload["crypto_orders"]:
        raise ValueError("Unsupported additional portfolio exposure")
    if set(reserved)-set(shares) or any(reserved.get(symbol, 0) > quantity for symbol, quantity in shares.items()):
        raise ValueError("Reserved shares exceed holdings")
    return total, shares, reserved, equity_quotes


def evaluate(candidate, limits, snapshot_payload, at):
    """Evaluate one hypothesis against trusted complete portfolio evidence.

    `max_aggregate_loss_usd` means conservative total remaining portfolio downside
    plus the proposal, not a claim about historical cost-basis performance. The
    covered-call stock downside is counted once; exits are never credited as fills.
    """
    reasons, calculations = [], {}
    payload = snapshot_payload
    try:
        feeds = payload.get("feed_metadata", {})
        if (payload.get("open_orders_complete") is not True or
                any(feeds.get(name, {}).get("complete") is not True or
                    not recent(feeds[name].get("received_at"), at) for name in REQUIRED_FEEDS)):
            reasons.append("complete_fresh_portfolio_feeds_required")
        if not recent(payload.get("portfolio_as_of"), at):
            reasons.append("portfolio_source_timestamp_required")
        strategy = STRATEGIES[candidate.setup]
        if strategy not in limits["strategies"]:
            reasons.append("strategy_outside_mandate")
        if candidate.symbol in limits.get("excluded_symbols", []):
            reasons.append("symbol_excluded")
        if limits["universe"] == "selected_symbols" and candidate.symbol not in limits["symbols"]:
            reasons.append("symbol_outside_mandate")
        # Free-text exclusions cannot be silently declared satisfied by arithmetic.
        if limits.get("exclusions"):
            reasons.append("free_text_exclusions_require_independent_classification")
        listing = payload.get("underlying_instruments", {}).get(candidate.symbol, {})
        if (listing.get("country") != "US" or listing.get("asset_type") not in {"equity", "etf"}
                or not recent(listing.get("verified_at"), at)):
            reasons.append("us_listed_underlying_unverified")
        deadline = next(x.deadline for x in candidate.exits if x.purpose == "time_exit")
        holding = (deadline-at).total_seconds()/86400 if deadline else -1
        if not limits["min_holding_days"] <= holding <= limits["max_holding_days"]:
            reasons.append("holding_period_outside_mandate")
        if deadline and any(deadline.date() >= x.contract.expiration for x in candidate.legs):
            reasons.append("time_exit_must_precede_expiration_day")
        account = payload.get("account", {})
        level = account.get("user_option_level") or account.get("option_level")
        if level not in ({"option_level_3"} if candidate.setup == "credit_spread" else {"option_level_2", "option_level_3"}):
            reasons.append("options_eligibility_unverified")
        trusted_specs = {x["id"]: Contract.model_validate(x) for x in payload.get("contracts", [])}
        trusted_quotes = _quotes(payload.get("option_quotes", []), at)
        for leg in candidate.legs:
            if leg.contract.strike <= 0 or leg.contract.expiration <= at.date():
                reasons.append("invalid_or_expiring_contract")
            actual = trusted_specs.get(leg.contract.id)
            if (actual is None or actual != leg.contract or not recent(actual.observed_at, at)):
                reasons.append("exact_broker_contract_unverified")
            quote = trusted_quotes.get(leg.contract.id)
            if quote is None or quote != leg.quote:
                reasons.append("exact_broker_quote_unverified")
        # Source-backed broker UI/order capability must be independently persisted,
        # not merely asserted in a supplied candidate's exit description.
        capabilities = payload.get("exit_capabilities", [])
        for exit_trigger in candidate.exits:
            if not any(x.get("setup") == candidate.setup and x.get("mechanism") == exit_trigger.mechanism
                       and x.get("basis") == exit_trigger.basis and x.get("action") == exit_trigger.action
                       and x.get("source") in {"broker_verified", "app_verified"}
                       and recent(x.get("verified_at"), at) for x in capabilities):
                reasons.append("exit_support_unverified")
        envelope = loss_envelope(candidate)
        calculations["payoff"] = envelope
        if not envelope["known"]:
            reasons.append("standard_payoff_unavailable")
        else:
            loss = amount(envelope["max_loss_usd"])
            collateral = amount(envelope["collateral_required_usd"])
            if loss > amount(limits["max_loss_per_trade_usd"]):
                reasons.append("per_trade_loss_limit_exceeded")
            if collateral > amount(limits["proposed_budget_usd"]):
                reasons.append("proposed_budget_exceeded")
            if collateral > amount(limits["max_collateral_usd"]):
                reasons.append("collateral_limit_exceeded")
            existing, shares, reserved, equity_quotes = _existing_exposure(payload, at)
            aggregate = existing + loss
            if candidate.setup == "covered_call":
                needed = Decimal(candidate.legs[0].quantity * 100)
                available = shares.get(candidate.symbol, Decimal(0)) - reserved.get(candidate.symbol, Decimal(0))
                if available < needed:
                    reasons.append("insufficient_uncommitted_shares")
                if candidate.symbol not in equity_quotes or candidate.underlying_reference_price != equity_quotes[candidate.symbol].ask or not recent(candidate.underlying_reference_at, at, 60):
                    reasons.append("covered_call_underlying_mark_unverified")
                # Existing stock is already in the portfolio downside. Do not
                # add it a second time or credit an unreceived option premium.
                aggregate = existing + candidate.fees_usd + candidate.slippage_usd
                calculations["uncommitted_shares"] = str(max(Decimal(0), available))
            else:
                cash = payload.get("assignment_cash", {})
                if (cash.get("semantics") != "broker_available_after_all_reservations" or
                        not recent(cash.get("source_as_of"), at)):
                    reasons.append("available_cash_semantics_unverified")
                elif amount(cash.get("available_usd")) < collateral:
                    reasons.append("insufficient_available_cash")
                # Available cash already reflects reservations; subtracting open
                # orders here again would count the same reservation twice.
            if aggregate > amount(limits["max_aggregate_loss_usd"]):
                reasons.append("aggregate_loss_limit_exceeded")
            calculations.update(existing_remaining_downside_usd=str(existing),
                                aggregate_remaining_downside_usd=str(aggregate))
    except (ValueError, TypeError, KeyError, StopIteration, InvalidOperation, ArithmeticError):
        reasons.append("portfolio_risk_inputs_unavailable_or_unrecognized")
    return {"allowed": not reasons, "reasons": sorted(set(reasons)), "calculations": calculations,
            "risk_basis": "conservative_remaining_portfolio_downside", "realized_profit_usd": None,
            "assignment_warning": "Spread loss is theoretical; assignment, pin risk and failed exits can create further exposure.",
            "execution": "manual_robinhood"}
