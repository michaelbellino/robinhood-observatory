"""Owner order controls: synthetic broker transport, never real financial activity."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from observatory import manual_orders as manual, trade_review as review
from observatory.db import TradeIdea, now
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


@pytest.mark.parametrize("terms", [
    stock(market_hours="extended_hours"), stock(market_hours="all_day_hours", time_in_force="gtc"),
    stock(type="stop_limit", stop_price="0.9", time_in_force="gtc"),
    stock(type="stop_market", stop_price="0.9", limit_price=None),
    stock(type="market", quantity="0.25", limit_price=None),
    stock(type="market", quantity=None, dollar_amount="1.00", limit_price=None),
])
async def test_expanded_stock_orders_preserve_exact_terms_once(configured, settings, vault, transport, terms):
    idea, body = ready(configured, settings, custom_terms=terms)
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "acknowledged", result
    sent = transport.sent[0][1]
    expected = review.Terms.model_validate(terms).broker_arguments("SYNTHETIC-ACCOUNT")
    assert {k: v for k, v in sent.items() if k != "ref_id"} == expected
    repeated = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert repeated["ref_id"] == result["ref_id"] and len(transport.sent) == 1


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
async def test_broker_restrictions_cannot_be_bypassed(configured, settings, vault, transport, change):
    idea, body = ready(configured, settings, custom_terms=stock(market_hours="extended_hours"))
    transport.tradability_changes = change
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "not_sent" and not transport.sent


@pytest.mark.parametrize("change", [
    {"market_hours": "regular_hours"}, {"time_in_force": "gfd"}, {"stop_price": "9"},
])
async def test_mismatched_response_is_unknown_and_never_retried(configured, settings, vault, transport, change):
    terms = stock(market_hours="extended_hours", time_in_force="gtc")
    idea, body = ready(configured, settings, custom_terms=terms)
    transport.order_changes = change
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "unknown"
    await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert len(transport.sent) == 1


async def test_stop_response_trigger_is_not_lost(configured, settings, vault, transport):
    idea, body = ready(configured, settings, custom_terms=stock(type="stop_limit", stop_price="0.9"))
    transport.order_changes = {"trigger": "immediate"}
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "unknown"


@pytest.mark.parametrize("market", [False, True])
async def test_old_quote_does_not_block_price_capped_limit_but_blocks_market(configured, settings, vault, transport, market):
    terms = stock(type="market", limit_price=None) if market else stock()
    idea, body = ready(configured, settings, custom_terms=terms)
    with configured.session() as s:
        row = s.get(TradeIdea, idea["id"])
        row.preview = {**row.preview, "quote_data": {**row.preview["quote_data"],
                        "venue_bid_time": (now()-timedelta(minutes=5)).isoformat()}}
        body.preview_hash = review.fingerprint(row.preview)
    if market:
        with pytest.raises(HTTPException, match="Refresh"):
            await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
        assert not transport.sent
    else:
        result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
        assert result["state"] == "acknowledged"


async def test_alert_ack_is_bound_to_exact_preview(configured, settings, vault, transport):
    idea, body = ready(configured, settings)
    with configured.session() as s:
        row = s.get(TradeIdea, idea["id"])
        row.preview = {**row.preview, "order_checks": {"alert_type": "FIXTURE_WARNING", "details": "Synthetic warning"}}
        body.preview_hash = review.fingerprint(row.preview)
    with pytest.raises(HTTPException, match="acknowledge"):
        manual.reserve(configured, settings.owner_subject, idea["id"], body)
    body.acknowledge_broker_alerts = True
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "acknowledged"
    assert "acknowledge_broker_alerts" not in transport.sent[0][1]


@pytest.mark.parametrize("hours", ["regular_curb_hours", "regular_curb_overnight_hours"])
@pytest.mark.parametrize("underlyings", [None, []])
async def test_index_sessions_verified_with_broker(configured, settings, vault, transport, hours, underlyings):
    terms = options(underlying_type="index", market_hours=hours, time_in_force="gtc")
    idea, body = ready(configured, settings, "option", terms)
    transport.extended_chain = False
    transport.chain_underlyings = underlyings
    blocked = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert blocked["state"] == "not_sent" and not transport.sent
    transport.extended_chain = True
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "acknowledged"
    assert transport.sent[0][1]["market_hours"] == hours


def spread():
    terms = options(direction="debit")
    terms["legs"].append({**terms["legs"][0], "option_id": str(uuid4()), "strike": "105", "side": "sell"})
    return terms


@pytest.mark.parametrize("account", [{}, {"option_level": "option_level_2"}, {"type": "cash"}, {"brokerage_account_type": "ira_roth"}])
async def test_spread_requires_broker_level_and_account(configured, settings, vault, transport, account):
    idea, body = ready(configured, settings, "option", spread())
    transport.account_changes = account
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == ("not_sent" if account else "acknowledged")
    assert len(transport.sent) == (0 if account else 1)


@pytest.mark.parametrize("side,position_type", [("sell", "long"), ("buy", "short")])
async def test_option_close_requires_correct_holding(configured, settings, vault, transport, side, position_type):
    terms = options()
    terms["legs"][0].update(side=side, position_effect="close")
    idea, body = ready(configured, settings, "option", terms)
    blocked = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert blocked["state"] == "not_sent" and not transport.sent
    transport.positions = [{"option_id": terms["legs"][0]["option_id"], "type": position_type, "quantity": "1"}]
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "acknowledged"


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
async def test_single_leg_option_types_preserve_price_and_trigger(configured, settings, vault, transport, order_type):
    terms = options()
    terms["type"] = order_type
    if order_type != "stop_limit":
        terms["limit_price"] = None
    if order_type.startswith("stop_"):
        terms["stop_price"] = "0.08"
    if order_type == "stop_market":
        terms["legs"][0].update(side="sell", position_effect="close")
        transport.positions = [{"option_id": terms["legs"][0]["option_id"], "type": "long", "quantity": "1"}]
    idea, body = ready(configured, settings, "option", terms)
    result = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert result["state"] == "acknowledged", result
    sent = transport.sent[0][1]
    assert sent["type"] == order_type
    assert ("price" in sent) == (order_type == "stop_limit")
    assert ("stop_price" in sent) == order_type.startswith("stop_")


def test_owner_controls_render_exact_terms_and_require_explicit_clicks():
    import subprocess
    subprocess.run(["node", "tests/order_controls_ui.mjs"], check=True, capture_output=True)
