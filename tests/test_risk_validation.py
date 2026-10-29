"""Synthetic deterministic risk gates; no account data, network or brokerage writes."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest

from observatory.risk_validation import REQUIRED_FEEDS, evaluate
from test_research import AT, candidate


def limits(**changes):
    values = {"strategies": ["defined_risk_credit_spread", "covered_call", "cash_secured_put", "long_call", "long_put"],
              "universe": "selected_symbols", "symbols": ["TEST"], "excluded_symbols": [], "exclusions": [],
              "proposed_budget_usd": "20000", "max_loss_per_trade_usd": "20000",
              "max_aggregate_loss_usd": "30000", "max_collateral_usd": "20000",
              "min_holding_days": 0, "max_holding_days": 7}
    values.update(changes)
    return values


def snapshot(idea):
    return {"portfolio_as_of": AT.isoformat(), "open_orders_complete": True,
            "feed_metadata": {name: {"complete": True, "received_at": AT.isoformat()} for name in REQUIRED_FEEDS},
            "account": {"option_level": "option_level_3"},
            "portfolio": {name: "0" for name in ("futures_value", "event_contracts_value", "fixed_income_value", "mutual_funds_value")},
            "equity_positions": [], "option_positions": [], "equity_orders": [], "option_orders": [],
            "advanced_orders": [], "crypto_positions": [], "crypto_orders": [], "quotes": [],
            "contracts": [x.contract.model_dump(mode="json") for x in idea.legs],
            "option_quotes": [x.quote.model_dump(mode="json") for x in idea.legs],
            "underlying_instruments": {"TEST": {"country": "US", "asset_type": "equity", "verified_at": AT.isoformat()}},
            "assignment_cash": {"semantics": "broker_available_after_all_reservations", "available_usd": "20000", "source_as_of": AT.isoformat()},
            "exit_capabilities": [{"setup": idea.setup, "basis": x.basis, "mechanism": x.mechanism, "action": x.action,
                                   "source": "app_verified", "verified_at": AT.isoformat()} for x in idea.exits]}


@pytest.mark.parametrize("setup,loss,collateral", [("long_call", "51.50", "51.50"), ("long_put", "51.50", "51.50"),
                                                   ("credit_spread", "51.50", "51.50"), ("cash_secured_put", "9951.50", "10000")])
def test_calculates_supported_standard_structures(setup, loss, collateral):
    idea = candidate(setup)
    result = evaluate(idea, limits(), snapshot(idea), AT)
    assert result["allowed"], result
    assert Decimal(result["calculations"]["payoff"]["max_loss_usd"]) == Decimal(loss)
    assert Decimal(result["calculations"]["payoff"]["collateral_required_usd"]) == Decimal(collateral)
    assert result["realized_profit_usd"] is None


def covered_fixture():
    idea = candidate("covered_call")
    data = snapshot(idea)
    data["equity_positions"] = [{"symbol": "TEST", "quantity": "100", "type": "long"}]
    data["quotes"] = [{"instrument_id": "TEST", "bid": "97.90", "ask": "98", "bid_at": AT.isoformat(),
                       "ask_at": AT.isoformat(), "received_at": AT.isoformat()}]
    return idea, data


def test_covered_call_counts_underlying_downside_once():
    idea, data = covered_fixture()
    result = evaluate(idea, limits(), data, AT)
    assert result["allowed"], result
    calc = result["calculations"]
    assert Decimal(calc["existing_remaining_downside_usd"]) == 9800
    assert Decimal(calc["aggregate_remaining_downside_usd"]) == Decimal("9801.50")
    assert calc["uncommitted_shares"] == "100"
    assert Decimal(calc["payoff"]["max_loss_usd"]) == Decimal("9751.50")


def test_existing_exit_quantity_cannot_be_reused_for_coverage():
    idea, data = covered_fixture()
    data["equity_orders"] = [{"symbol": "TEST", "quantity": "50", "cumulative_quantity": "10", "side": "sell", "state": "partially_filled"}]
    result = evaluate(idea, limits(), data, AT)
    assert not result["allowed"] and "insufficient_uncommitted_shares" in result["reasons"]
    assert result["calculations"]["uncommitted_shares"] == "60"


def test_cash_secured_put_requires_full_assignment_and_does_not_use_buying_power():
    idea = candidate("cash_secured_put")
    data = snapshot(idea)
    data["assignment_cash"]["available_usd"] = "9999.99"
    data["portfolio"]["buying_power"] = {"buying_power": "999999"}
    result = evaluate(idea, limits(), data, AT)
    assert not result["allowed"] and "insufficient_available_cash" in result["reasons"]
    assert result["calculations"]["payoff"]["assignment_obligation_usd"] == "10000"


def test_available_cash_is_not_double_subtracted_for_pending_buy_reservations():
    idea = candidate("cash_secured_put")
    data = snapshot(idea)
    data["assignment_cash"]["available_usd"] = "10000"
    data["equity_orders"] = [{"symbol": "TEST", "quantity": "10", "cumulative_quantity": "0", "side": "buy",
                              "type": "limit", "price": "5", "state": "confirmed"}]
    result = evaluate(idea, limits(), data, AT)
    assert result["allowed"], result
    assert Decimal(result["calculations"]["aggregate_remaining_downside_usd"]) == Decimal("10001.50")


@pytest.mark.parametrize("mutate,reason", [
    (lambda x: x.update(open_orders_complete=False), "complete_fresh_portfolio_feeds_required"),
    (lambda x: x.update(portfolio_as_of=None), "portfolio_source_timestamp_required"),
    (lambda x: x["feed_metadata"]["advanced_orders"].update(complete=False), "complete_fresh_portfolio_feeds_required"),
    (lambda x: x["assignment_cash"].update(semantics="buying_power"), "available_cash_semantics_unverified"),
    (lambda x: x.update(exit_capabilities=[]), "exit_support_unverified"),
    (lambda x: x["contracts"][0].update(deliverable="10 shares"), "exact_broker_contract_unverified"),
    (lambda x: x["option_quotes"][0].update(ask_at=(AT-timedelta(minutes=2)).isoformat()), "portfolio_risk_inputs_unavailable_or_unrecognized"),
    (lambda x: x.update(crypto_positions=[{"quantity": "1"}]), "portfolio_risk_inputs_unavailable_or_unrecognized"),
    (lambda x: x["portfolio"].update(futures_value="1"), "portfolio_risk_inputs_unavailable_or_unrecognized"),
    (lambda x: x.update(option_orders=[{"state": "queued"}]), "portfolio_risk_inputs_unavailable_or_unrecognized"),
])
def test_unknown_stale_nonstandard_and_unsupported_inputs_block(mutate, reason):
    idea = candidate()
    data = snapshot(idea)
    mutate(data)
    result = evaluate(idea, limits(), data, AT)
    assert not result["allowed"] and reason in result["reasons"], result


@pytest.mark.parametrize("changes,reason", [
    ({"max_loss_per_trade_usd": "50"}, "per_trade_loss_limit_exceeded"),
    ({"max_aggregate_loss_usd": "50"}, "aggregate_loss_limit_exceeded"),
    ({"max_collateral_usd": "50"}, "collateral_limit_exceeded"),
    ({"proposed_budget_usd": "50"}, "proposed_budget_exceeded"),
    ({"strategies": ["covered_call"]}, "strategy_outside_mandate"),
    ({"excluded_symbols": ["TEST"]}, "symbol_excluded"),
    ({"symbols": ["OTHER"]}, "symbol_outside_mandate"),
    ({"max_holding_days": 1}, "holding_period_outside_mandate"),
])
def test_mandate_limits_are_actually_enforced(changes, reason):
    idea = candidate()
    result = evaluate(idea, limits(**changes), snapshot(idea), AT)
    assert not result["allowed"] and reason in result["reasons"]


def test_existing_long_option_risk_consumes_aggregate_capacity():
    idea = candidate()
    data = snapshot(idea)
    data["option_positions"] = [{"option_id": idea.legs[0].contract.id, "type": "long", "quantity": "1"}]
    result = evaluate(idea, limits(max_aggregate_loss_usd="100"), data, AT)
    assert not result["allowed"] and "aggregate_loss_limit_exceeded" in result["reasons"]
    assert Decimal(result["calculations"]["existing_remaining_downside_usd"]) == 60


def test_existing_short_option_is_not_silently_assumed_covered():
    idea = candidate()
    data = snapshot(idea)
    data["option_positions"] = [{"option_id": idea.legs[0].contract.id, "type": "short", "quantity": "1"}]
    assert not evaluate(idea, limits(), data, AT)["allowed"]


def test_nonstandard_candidate_never_inherits_standard_payoff():
    value = candidate().model_dump()
    value["legs"][0]["contract"]["deliverable"] = "adjusted basket"
    from observatory.research import ResearchCandidate
    idea = ResearchCandidate.model_validate(deepcopy(value))
    result = evaluate(idea, limits(), snapshot(idea), AT)
    assert not result["allowed"] and "standard_payoff_unavailable" in result["reasons"]
