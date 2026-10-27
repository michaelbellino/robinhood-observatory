"""Order controls use synthetic data and never submit an order."""
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from observatory import manual_orders as manual, trade_review as review
from tests import test_manual_orders
from tests.test_manual_orders import ready
from tests.test_rehearsal import owner_session

transport = test_manual_orders.transport


def stock(**changes):
    result = dict(asset="equity", symbol="TEST", side="buy", quantity="1", limit_price="1", type="limit")
    result.update(changes)
    return result


def options(**changes):
    leg = dict(option_id=str(uuid4()), expiration="2030-01-18", strike="100", right="call",
               side="buy", position_effect="open", multiplier=100)
    return dict(asset="option", symbol="TEST", quantity="1", limit_price="0.10", legs=[leg], **changes)


def spread():
    terms = options(direction="debit")
    terms["legs"].append({**terms["legs"][0], "option_id": str(uuid4()), "strike": "105", "side": "sell"})
    return terms


@pytest.mark.parametrize("terms", [
    stock(market_hours="extended_hours"), stock(market_hours="all_day_hours", time_in_force="gtc"),
    stock(type="stop_limit", stop_price="0.9", time_in_force="gtc"),
    stock(type="stop_market", stop_price="0.9", limit_price=None),
    stock(type="market", quantity="0.25", limit_price=None),
    stock(type="market", quantity=None, dollar_amount="1.00", limit_price=None),
    options(), spread(),
])
async def test_valid_terms_cannot_trigger_hosted_placement(configured, settings, vault, transport, terms):
    validated = review.Terms.model_validate(terms)
    idea, body = ready(configured, settings, validated.asset, terms)
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
        assert error.value.status_code == 410
    assert not transport.sent and transport.initializations == 0


@pytest.mark.parametrize("terms", [
    stock(type="market", limit_price=None, market_hours="extended_hours"),
    stock(type="stop_limit", stop_price="1", market_hours="all_day_hours"),
    stock(quantity="0.5"), stock(quantity=None, dollar_amount="1"),
    stock(dollar_amount="1"), stock(quantity=None), stock(stop_price="1"),
    stock(type="stop_limit"), stock(type="market"), stock(quantity=True),
    stock(market_hours="regular_curb_hours"),
])
def test_broker_incompatible_stock_shapes_fail_before_network(terms):
    with pytest.raises(ValueError):
        review.Terms.model_validate(terms)


@pytest.mark.parametrize("change", [
    {"all_day_tradability": "untradable"}, {"state": "inactive"}, {"tradeable": False},
    {"account_type_tradabilities": [{"account_type": "individual", "account_type_tradability": "position_closing_only"}]},
])
async def test_eligibility_read_rejects_broker_restrictions(transport, change):
    transport.tradability_changes = change
    broker = transport.factory("synthetic", "SYNTHETIC-ACCOUNT", 6)
    try:
        await broker.initialize()
        with pytest.raises(HTTPException):
            await manual.eligibility(broker, "SYNTHETIC-ACCOUNT", review.Terms.model_validate(stock(market_hours="extended_hours")))
        assert not transport.sent
    finally:
        await broker.close()


@pytest.mark.parametrize("hours", ["regular_curb_hours", "regular_curb_overnight_hours"])
@pytest.mark.parametrize("underlyings", [None, []])
async def test_index_sessions_require_broker_eligibility(transport, hours, underlyings):
    terms = review.Terms.model_validate(options(underlying_type="index", market_hours=hours, time_in_force="gtc"))
    transport.chain_underlyings = underlyings
    transport.extended_chain = False
    broker = transport.factory("synthetic", "SYNTHETIC-ACCOUNT", 6)
    try:
        await broker.initialize()
        with pytest.raises(HTTPException):
            await manual.eligibility(broker, "SYNTHETIC-ACCOUNT", terms)
        transport.extended_chain = True
        await manual.eligibility(broker, "SYNTHETIC-ACCOUNT", terms)
        assert not transport.sent
    finally:
        await broker.close()


@pytest.mark.parametrize("account", [{"option_level": "option_level_2"}, {"type": "cash"}, {"brokerage_account_type": "ira_roth"}])
async def test_spread_eligibility_requires_broker_level_and_account(transport, account):
    transport.account_changes = account
    broker = transport.factory("synthetic", "SYNTHETIC-ACCOUNT", 6)
    try:
        await broker.initialize()
        with pytest.raises(HTTPException):
            await manual.eligibility(broker, "SYNTHETIC-ACCOUNT", review.Terms.model_validate(spread()))
        assert not transport.sent
    finally:
        await broker.close()


@pytest.mark.parametrize("side,position_type", [("sell", "long"), ("buy", "short")])
async def test_option_close_eligibility_requires_correct_holding(transport, side, position_type):
    terms = options()
    terms["legs"][0].update(side=side, position_effect="close")
    broker = transport.factory("synthetic", "SYNTHETIC-ACCOUNT", 6)
    try:
        await broker.initialize()
        with pytest.raises(HTTPException):
            await manual.eligibility(broker, "SYNTHETIC-ACCOUNT", review.Terms.model_validate(terms))
        transport.positions = [{"option_id": terms["legs"][0]["option_id"], "type": position_type, "quantity": "1"}]
        await manual.eligibility(broker, "SYNTHETIC-ACCOUNT", review.Terms.model_validate(terms))
        assert not transport.sent
    finally:
        await broker.close()


def test_option_session_and_contract_constraints():
    for changes in ({"market_hours": "regular_curb_hours"}, {"quantity": "0.5"}):
        terms = options()
        terms.update(changes)
        with pytest.raises(ValueError):
            review.Terms.model_validate(terms)
    terms = spread()
    terms.update(type="stop_limit", stop_price="1")
    with pytest.raises(ValueError):
        review.Terms.model_validate(terms)


def test_owner_ticket_has_no_research_cap_or_required_rationale(client, configured, settings, control_headers):
    body = {"request_id": str(uuid4()), "terms": stock()}
    path = "/api/trades/owner-ticket"
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, headers=control_headers, json=body).status_code == 403
    headers = owner_session(client, settings)
    assert client.post(path, json=body).status_code == 403
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 200 and first.json()["sent_to_broker"] is False
    assert client.post(path, headers=headers, json=body).json()["id"] == first.json()["id"]
    for index in range(31):
        body = {"request_id": str(uuid4()), "terms": stock(limit_price=str(Decimal("2")+index))}
        assert client.post(path, headers=headers, json=body).status_code == 200


@pytest.mark.parametrize("order_type", ["market", "stop_limit", "stop_market"])
def test_single_leg_option_type_preserves_price_and_trigger(order_type):
    terms = options()
    terms["type"] = order_type
    if order_type != "stop_limit":
        terms["limit_price"] = None
    if order_type.startswith("stop_"):
        terms["stop_price"] = "0.08"
    if order_type == "stop_market":
        terms["legs"][0].update(side="sell", position_effect="close")
    args = review.Terms.model_validate(terms).broker_arguments("SYNTHETIC-ACCOUNT")
    assert args["type"] == order_type
    assert ("price" in args) == (order_type == "stop_limit")
    assert ("stop_price" in args) == order_type.startswith("stop_")


def test_owner_controls_render_exact_terms_and_have_no_submission_action():
    import subprocess
    subprocess.run(["node", "tests/order_controls_ui.mjs"], check=True, capture_output=True)
