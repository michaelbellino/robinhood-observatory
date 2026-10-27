"""Manual review and legacy-status reconciliation; synthetic records, never broker I/O."""
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from observatory import manual_orders as orders, trade_review as review
from observatory.db import State, TradeIdea, OrderSubmission, Event, now
from tests.test_manual_orders import ready
from tests.test_rehearsal import owner_session


def revision(idea, **terms):
    return review.Revision(request_id=uuid4(), terms_hash=idea["terms_hash"],
                           terms={**idea["payload"]["terms"], **terms})


def decision(idea, approval):
    return review.Decision(request_id=uuid4(), terms_hash=idea["terms_hash"],
                           preview_hash=approval.preview_hash, decision="prepare_in_robinhood",
                           acknowledge_manual_submission=True)


def legacy_submission(db, settings, idea, approval, *, state="unknown"):
    """Represent an existing old-release submission; never call a broker or reserve a new one."""
    ref_id = str(uuid4())
    order_id = str(uuid4())
    terms = review.Terms.model_validate(idea["payload"]["terms"])
    args = terms.broker_arguments("SYNTHETIC-ACCOUNT")
    order = {**args, "id": order_id, "ref_id": ref_id, "state": "queued",
             "trigger": "immediate", "price": args["limit_price"],
             "cumulative_quantity": "0", "processed_quantity": "0"}
    with db.session() as s:
        generation = s.get(State, 1).generation
        row = s.get(TradeIdea, idea["id"])
        row.status = "submission_unknown" if state == "unknown" else "submitted_by_owner"
        if state == "acknowledged":
            row.broker_order_id = order_id
        s.add(OrderSubmission(idea_id=idea["id"], ref_id=ref_id, actor=settings.owner_subject,
            request_hash=review.fingerprint(approval.model_dump(mode="json")), generation=generation,
            state=state, started_at=now()-timedelta(minutes=1), updated_at=now()-timedelta(seconds=1),
            approved_at=now()-timedelta(minutes=1), approval={"version": 1, "terms_hash": idea["terms_hash"],
                "terms": idea["payload"]["terms"]}, order=order if state == "acknowledged" else None))
    return order


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
    with pytest.raises(HTTPException, match="account or connection changed"):
        review.decide(configured, settings.owner_subject, idea["id"], decision(idea, approval))


def test_manual_review_records_exact_terms_without_submission(configured, settings):
    idea, approval = ready(configured, settings)
    result = review.decide(configured, settings.owner_subject, idea["id"], decision(idea, approval))
    assert result["status"] == "ready_for_manual_submission"
    assert result["payload"]["terms"] == idea["payload"]["terms"]
    assert result["terms_hash"] == idea["terms_hash"]
    assert result["sent_to_broker"] is None  # Manual placement is not observable from review alone.
    with configured.session() as s:
        assert s.get(OrderSubmission, idea["id"]) is None
    with pytest.raises(HTTPException):
        review.revise(configured, settings.owner_subject, idea["id"], revision(idea, quantity="2"))


@pytest.mark.parametrize("state,expected", [("partially_filled", "partially_filled"), ("filled", "filled"),
    ("rejected", "rejected"), ("cancelled", "cancelled"), ("expired", "expired"),
    ("partially_filled_rest_cancelled", "partially_filled_then_cancelled"),
    ("unconfirmed", "submitted"), ("future_new_state", "broker_status_unrecognized")])
def test_legacy_broker_states_and_notification(configured, settings, state, expected):
    idea, approval = ready(configured, settings)
    order = legacy_submission(configured, settings, idea, approval, state="acknowledged")
    actual = {**order, "state": state, "cumulative_quantity": "0.5", "processed_quantity": "0.5"}
    with configured.session() as s:
        orders.reconcile_records(s, {"equity_orders": [actual]}, s.get(State, 1).account_cipher, now())
    result = orders.get_results(configured)[idea["id"]]
    assert result["order_status"] == expected and result["order"]["state"] == state
    with configured.session() as s:
        assert s.scalar(select(Event).where(Event.kind == "trade_order_status"))


def test_ambiguous_legacy_outcome_requires_exact_reference(configured, settings):
    idea, approval = ready(configured, settings)
    order = legacy_submission(configured, settings, idea, approval)
    observed = {**order, "id": str(uuid4())}
    observed.pop("ref_id")
    for rows in ([], [observed]):
        with configured.session() as s:
            orders.reconcile_records(s, {"equity_orders": rows}, s.get(State, 1).account_cipher, now())
        assert orders.get_results(configured)[idea["id"]]["state"] == "unknown"
    observed["ref_id"] = order["ref_id"]
    with configured.session() as s:
        orders.reconcile_records(s, {"equity_orders": [observed]}, s.get(State, 1).account_cipher, now())
    assert orders.get_results(configured)[idea["id"]]["state"] == "acknowledged"


def test_reconcile_crash_before_transmission_requires_new_approval(configured, settings):
    idea, approval = ready(configured, settings)
    legacy_submission(configured, settings, idea, approval, state="preparing")
    with configured.session() as s:
        s.get(OrderSubmission, idea["id"]).updated_at = now()-timedelta(minutes=4)
        orders.reconcile_records(s, {"equity_orders": []}, s.get(State, 1).account_cipher, now())
    assert orders.get_results(configured)[idea["id"]]["state"] == "not_sent"


def test_edit_and_reconcile_require_session_csrf_and_no_get_mutations(client, configured, settings, control_headers):
    idea, _ = ready(configured, settings)
    body = revision(idea).model_dump(mode="json")
    for suffix in ("revise", "reconcile"):
        path = f'/api/trades/{idea["id"]}/{suffix}'
        assert client.post(path, json=body if suffix == "revise" else {}).status_code == 401
        assert client.post(path, headers=control_headers, json=body if suffix == "revise" else {}).status_code == 403
    headers = owner_session(client, settings)
    assert client.get(f'/api/trades/{idea["id"]}/submit').status_code == 405
    assert client.post(f'/api/trades/{idea["id"]}/revise', json=body).status_code == 403
    assert client.post(f'/api/trades/{idea["id"]}/revise', headers=headers, json=body).status_code == 200


def test_stale_snapshot_cannot_regress_fills(configured, settings):
    idea, approval = ready(configured, settings)
    order = legacy_submission(configured, settings, idea, approval, state="acknowledged")
    filled = {**order, "state": "filled", "cumulative_quantity": "1", "processed_quantity": "1"}
    for observed in (filled, order):
        with configured.session() as s:
            orders.reconcile_records(s, {"equity_orders": [observed]}, s.get(State, 1).account_cipher, now())
    assert orders.get_results(configured)[idea["id"]]["order_status"] == "filled"


@pytest.mark.parametrize("state", ["preparing", "transmitting", "unknown", "acknowledged"])
async def test_disabled_submission_cannot_change_legacy_state(configured, settings, vault, state):
    idea, approval = ready(configured, settings)
    legacy_submission(configured, settings, idea, approval, state=state)
    before = orders.get_results(configured)[idea["id"]]
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            await orders.submit(configured, settings, vault, idea["id"], settings.owner_subject, approval)
        assert exc.value.status_code == 410
    assert orders.get_results(configured)[idea["id"]] == before
