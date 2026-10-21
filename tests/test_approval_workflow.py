"""Approval-to-status regression coverage. Synthetic data and HTTP transport only."""
from datetime import timedelta
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from observatory import manual_orders as orders, trade_review as review
from observatory.db import State, TradeIdea, OrderSubmission, Event, now
from tests import test_manual_orders
from tests.test_manual_orders import ready
from tests.test_rehearsal import owner_session

transport = test_manual_orders.transport


def revision(idea, **terms):
    return review.Revision(request_id=uuid4(), terms_hash=idea["terms_hash"],
                           terms={**idea["payload"]["terms"], **terms})


def test_version_edit_is_atomic_and_invalidates_preview(configured, settings):
    idea, approval = ready(configured, settings)
    body = revision(idea, quantity="2")
    edited = review.revise(configured, settings.owner_subject, idea["id"], body)
    assert edited["version"] == 2 and edited["parent_id"] == idea["id"]
    assert edited["preview"] is None and edited["account_bound"]
    assert review.revise(configured, settings.owner_subject, idea["id"], body)["id"] == edited["id"]
    with pytest.raises(HTTPException):
        orders.reserve(configured, settings.owner_subject, idea["id"], approval)
    with pytest.raises(HTTPException):
        review.revise(configured, settings.owner_subject, idea["id"], revision(idea, quantity="3"))
    with configured.session() as s:
        assert s.get(TradeIdea, idea["id"]).status == "superseded"


async def test_account_switch_before_preview_and_approval_is_blocked(configured, settings, vault):
    idea, approval = ready(configured, settings)
    with configured.session() as s:
        s.get(State, 1).account_cipher = vault.encrypt("DIFFERENT-SYNTHETIC-ACCOUNT")
    with pytest.raises(HTTPException, match="account changed"):
        orders.reserve(configured, settings.owner_subject, idea["id"], approval)
    with pytest.raises(HTTPException, match="account changed"):
        await review.preview(configured, settings, vault, idea["id"], settings.owner_subject)


async def test_approval_records_exact_version_and_terms(configured, settings, vault, transport):
    idea, approval = ready(configured, settings)
    result = await orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, approval, transport.factory)
    assert result["approved_by"] == settings.owner_subject and result["approved_at"]
    assert result["approval"]["terms"] == idea["payload"]["terms"]
    assert result["approval"]["terms_hash"] == idea["terms_hash"]
    assert result["order_status"] == "accepted"
    with pytest.raises(HTTPException):
        orders.reserve(configured, settings.owner_subject, idea["id"], approval.model_copy(update={"version": 2}))
    with pytest.raises(HTTPException):
        review.revise(configured, settings.owner_subject, idea["id"], revision(idea, quantity="2"))


@pytest.mark.parametrize("state,expected", [("partially_filled", "partially_filled"), ("filled", "filled"),
    ("rejected", "rejected"), ("cancelled", "cancelled"), ("expired", "expired"),
    ("partially_filled_rest_cancelled", "partially_filled_then_cancelled"),
    ("unconfirmed", "submitted"), ("future_new_state", "broker_status_unrecognized")])
async def test_actual_broker_states_and_notification(configured, settings, vault, transport, state, expected):
    idea, approval = ready(configured, settings)
    initial = await orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, approval, transport.factory)
    actual = {**initial["order"], "state": state, "cumulative_quantity": "0.5", "processed_quantity": "0.5"}
    with configured.session() as s:
        orders.reconcile_records(s, {"equity_orders": [actual]}, s.get(State, 1).account_cipher, now())
    result = orders.get_results(configured)[idea["id"]]
    assert result["order_status"] == expected and result["order"]["state"] == state
    assert len(transport.sent) == 1
    with configured.session() as s:
        assert s.scalar(select(Event).where(Event.kind == "trade_order_status"))


async def test_timeout_cannot_be_resolved_by_similar_order_or_absence(configured, settings, vault, transport):
    idea, approval = ready(configured, settings)
    transport.failure = "timeout"
    initial = await orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, approval, transport.factory)
    name, args = transport.sent[0]
    observed = {**args, "id": str(uuid4()), "state": "queued", "price": args["limit_price"]}
    observed.pop("ref_id")
    for rows in ([], [observed]):
        with configured.session() as s:
            orders.reconcile_records(s, {"equity_orders": rows}, s.get(State, 1).account_cipher, now())
        assert orders.get_results(configured)[idea["id"]]["state"] == "unknown"
    # Only an exact upstream reference, if supplied by the provider, resolves an ambiguous write.
    observed["ref_id"] = initial["ref_id"]
    with configured.session() as s:
        orders.reconcile_records(s, {"equity_orders": [observed]}, s.get(State, 1).account_cipher, now())
    assert orders.get_results(configured)[idea["id"]]["state"] == "acknowledged"
    assert len(transport.sent) == 1


def test_reconcile_crash_before_transmission_requires_new_approval(configured, settings):
    idea, approval = ready(configured, settings)
    orders.reserve(configured, settings.owner_subject, idea["id"], approval)
    with configured.session() as s:
        sub = s.get(OrderSubmission, idea["id"])
        sub.updated_at = now()-timedelta(minutes=4)
        orders.reconcile_records(s, {"equity_orders": []}, s.get(State, 1).account_cipher, now())
    assert orders.get_results(configured)[idea["id"]]["state"] == "not_sent"


def test_edit_and_reconcile_require_session_csrf_and_no_get_mutations(client, configured, settings, control_headers):
    idea, approval = ready(configured, settings)
    body = revision(idea).model_dump(mode="json")
    for suffix in ("revise", "reconcile"):
        path = f'/api/trades/{idea["id"]}/{suffix}'
        assert client.post(path, json=body if suffix == "revise" else {}).status_code == 401
        assert client.post(path, headers=control_headers, json=body if suffix == "revise" else {}).status_code == 403
    headers = owner_session(client, settings)
    assert client.get(f'/api/trades/{idea["id"]}/submit').status_code == 405
    assert client.post(f'/api/trades/{idea["id"]}/revise', json=body).status_code == 403
    assert client.post(f'/api/trades/{idea["id"]}/revise', headers=headers, json=body).status_code == 200


async def test_late_preparation_cannot_erase_newer_approval(configured, settings, vault, transport):
    idea, approval = ready(configured, settings)
    replacement = []

    def replace_approval():
        with configured.session() as s:
            s.get(OrderSubmission, idea["id"]).updated_at = now()-timedelta(minutes=4)
            orders.reconcile_records(s, {"equity_orders": []}, s.get(State, 1).account_cipher, now())
        replacement.append(orders.reserve(configured, settings.owner_subject, idea["id"], approval))

    transport.before_send = replace_approval
    result = await orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, approval, transport.factory)
    assert result["state"] == "preparing"
    assert replacement[0][0] is not None and not transport.sent
    assert result["approval"]["attempt_id"] == replacement[0][0][-1]


async def test_stale_snapshot_cannot_regress_fills(configured, settings, vault, transport):
    idea, approval = ready(configured, settings)
    initial = await orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, approval, transport.factory)
    filled = {**initial["order"], "state": "filled", "cumulative_quantity": "1", "processed_quantity": "1"}
    for observed in (filled, initial["order"]):
        with configured.session() as s:
            orders.reconcile_records(s, {"equity_orders": [observed]}, s.get(State, 1).account_cipher, now())
    assert orders.get_results(configured)[idea["id"]]["order_status"] == "filled"


@pytest.mark.parametrize("failure", [None, "timeout"])
async def test_late_response_cannot_overwrite_reconciled_fill(configured, settings, vault, transport, failure):
    idea, approval = ready(configured, settings)
    transport.response_gate = asyncio.Event()
    transport.failure = failure
    task = asyncio.create_task(orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, approval, transport.factory))
    try:
        for _ in range(100):
            if transport.sent:
                break
            await asyncio.sleep(0.001)
        assert transport.sent
        args = transport.sent[0][1]
        filled = {**args, "id": str(uuid4()), "state": "filled", "price": args["limit_price"],
                  "cumulative_quantity": "1", "processed_quantity": "1"}
        with configured.session() as s:
            orders.reconcile_records(s, {"equity_orders": [filled]}, s.get(State, 1).account_cipher, now())
        transport.response_gate.set()
        result = await task
        assert result["order_status"] == "filled" and result["order"]["id"] == filled["id"]
        assert len(transport.sent) == 1
    finally:
        transport.response_gate.set()
        await task
