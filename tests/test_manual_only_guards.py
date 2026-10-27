"""Fail-closed guards for manual Robinhood placement; synthetic fixtures only."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from observatory import manual_orders as orders, trade_review as review
from observatory.db import Snapshot, State, TradeIdea, now
from tests.test_manual_orders import ready
from tests.test_rehearsal import owner_session


async def test_hosted_submission_function_and_route_return_gone(client, configured, settings, vault):
    idea, body = ready(configured, settings)
    with pytest.raises(HTTPException) as failure:
        await orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, body)
    assert failure.value.status_code == 410
    response = client.post(f'/api/trades/{idea["id"]}/submit', headers=owner_session(client, settings),
                           json=body.model_dump(mode="json"))
    assert response.status_code == 410
    assert "place_equity_order" not in orders.OwnerOrderBroker.allowed
    assert "place_option_order" not in orders.OwnerOrderBroker.allowed


@pytest.mark.parametrize("change", ["account", "generation", "unverified"])
def test_manual_decision_rejects_account_changes(configured, settings, vault, change):
    idea, approval = ready(configured, settings)
    with configured.session() as session:
        state = session.get(State, 1)
        if change == "account":
            state.account_cipher = vault.encrypt("OTHER-SYNTHETIC-ACCOUNT")
        elif change == "generation":
            state.generation += 1
        else:
            state.account_verified = False
    decision = review.Decision(request_id=uuid4(), terms_hash=idea["terms_hash"],
                               preview_hash=approval.preview_hash, decision="prepare_in_robinhood",
                               acknowledge_manual_submission=True)
    with pytest.raises(HTTPException) as failure:
        review.decide(configured, settings.owner_subject, idea["id"], decision)
    assert failure.value.status_code == 409


@pytest.mark.parametrize("asset", ["equity", "option"])
def test_manual_decision_rejects_stale_quotes_even_for_limit(configured, settings, asset):
    idea, _ = ready(configured, settings, asset=asset)
    with configured.session() as session:
        row = session.get(TradeIdea, idea["id"])
        preview = dict(row.preview)
        stale = (now() - timedelta(minutes=5)).isoformat()
        if asset == "equity":
            preview["quote_data"] = {**preview["quote_data"], "venue_bid_time": stale, "venue_ask_time": stale}
        else:
            preview["option_quotes"] = [{**quote, "updated_at": stale} for quote in preview["option_quotes"]]
        row.preview = preview
        preview_hash = review.fingerprint(preview)
    decision = review.Decision(request_id=uuid4(), terms_hash=idea["terms_hash"], preview_hash=preview_hash,
                               decision="prepare_in_robinhood", acknowledge_manual_submission=True)
    with pytest.raises(HTTPException, match="stale") as failure:
        review.decide(configured, settings.owner_subject, idea["id"], decision)
    assert failure.value.status_code == 409


def test_order_linking_rejects_unbound_snapshot(configured, settings):
    idea, approval = ready(configured, settings)
    decision = review.Decision(request_id=uuid4(), terms_hash=idea["terms_hash"],
                               preview_hash=approval.preview_hash, decision="prepare_in_robinhood",
                               acknowledge_manual_submission=True)
    review.decide(configured, settings.owner_subject, idea["id"], decision)
    order_id = str(uuid4())
    with configured.session() as session:
        session.add(Snapshot(id=str(uuid4()), source="robinhood_mcp_live", payload={
            "equity_orders": [{"id": order_id, "symbol": "TEST", "side": "buy", "quantity": "1",
                               "price": "1", "type": "limit", "time_in_force": "gfd",
                               "market_hours": "regular_hours", "created_at": now().isoformat(),
                               "state": "filled"}]}))
    with pytest.raises(HTTPException) as failure:
        review.link_order(configured, settings.owner_subject, idea["id"], order_id)
    assert failure.value.status_code == 409
