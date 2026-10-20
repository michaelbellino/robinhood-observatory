"""Workflow tests use invented prices/accounts, never a brokerage endpoint."""
import base64
import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from itsdangerous import TimestampSigner
from sqlalchemy import select, func

from observatory import rehearsal
from observatory.db import Audit, Rehearsal, Snapshot, State, now


def body(asset="equity", **terms):
    return {"request_id": str(uuid.uuid4()), "terms": {**rehearsal.fixture(asset).model_dump(mode="json"), **terms}}


def owner_session(client, settings):
    # Test-only signed session, never generate a production cookie this way.
    session = {"sub": settings.owner_subject, "csrf": "fixture-csrf"}
    encoded = base64.b64encode(json.dumps(session).encode())
    client.cookies.set("observatory_session", TimestampSigner(settings.session_secret).sign(encoded).decode())
    return {"X-CSRF-Token": "fixture-csrf", "Origin": settings.public_url}


def review_body(ticket, **changes):
    return {"request_id": str(uuid.uuid4()), "terms_hash": ticket["terms_hash"],
            "decision": "reviewed", "acknowledge_paper_only": True, **changes}


def test_isolated_round_trip_and_idempotency(client, settings, control_headers, db):
    request = body()
    first = client.post("/api/rehearsals", headers=control_headers, json=request).json()
    assert first["status"] == "awaiting_paper_review"
    assert first["payload"]["paper_debit_usd"] == "1.00000000"
    replay = client.post("/api/rehearsals", headers=control_headers, json=request).json()
    assert replay["id"] == first["id"] and replay["replayed"]
    headers = owner_session(client, settings)
    action = review_body(first)
    reviewed = client.post(f'/api/rehearsals/{first["id"]}/review', headers=headers, json=action)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "paper_reviewed"
    assert not reviewed.json()["sent_to_broker"] and reviewed.json()["fill_status"] == "unfilled"
    replay = client.post(f'/api/rehearsals/{first["id"]}/review', headers=headers, json=action)
    assert replay.json()["replayed"]
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Rehearsal)) == 1
        assert s.scalar(select(func.count()).select_from(Audit)) == 2
        assert s.scalar(select(func.count()).select_from(Snapshot)) == 0
        assert s.get(State, 1).paused and not s.get(State, 1).account_verified


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer fixture-read-only"}])
def test_creation_authorization(client, headers):
    assert client.post("/api/rehearsals", headers=headers, json=body()).status_code in (401, 403)


def test_bearer_cannot_acknowledge_review(client, control_headers):
    ticket = client.post("/api/rehearsals", headers=control_headers, json=body()).json()
    response = client.post(f'/api/rehearsals/{ticket["id"]}/review', headers=control_headers, json=review_body(ticket))
    assert response.status_code == 403


def test_review_requires_csrf_and_origin(client, settings, control_headers):
    ticket = client.post("/api/rehearsals", headers=control_headers, json=body()).json()
    headers = owner_session(client, settings)
    path = f'/api/rehearsals/{ticket["id"]}/review'
    assert client.post(path, json=review_body(ticket)).status_code == 403
    assert client.post(path, headers={**headers, "Origin": "https://invalid.example"}, json=review_body(ticket)).status_code == 403


def test_review_changed_terms_expiry_and_conflicting_decisions(client, settings, control_headers, db):
    ticket = client.post("/api/rehearsals", headers=control_headers, json=body()).json()
    headers = owner_session(client, settings)
    path = f'/api/rehearsals/{ticket["id"]}/review'
    assert client.post(path, headers=headers, json=review_body(ticket, terms_hash="f"*64)).status_code == 409
    with db.session() as s:
        s.get(Rehearsal, ticket["id"]).expires_at = now() - timedelta(seconds=1)
    assert client.post(path, headers=headers, json=review_body(ticket)).status_code == 409
    assert client.get("/api/rehearsals").json()["tickets"][0]["status"] == "expired"
    fresh = client.post("/api/rehearsals", headers=headers, json=body()).json()
    path = f'/api/rehearsals/{fresh["id"]}/review'
    assert client.post(path, headers=headers, json=review_body(fresh, decision="rejected")).json()["status"] == "paper_rejected"
    assert client.post(path, headers=headers, json=review_body(fresh)).status_code == 409


def test_altered_payload_cannot_reuse_hash(client, settings, control_headers, db):
    ticket = client.post("/api/rehearsals", headers=control_headers, json=body()).json()
    with db.session() as s:
        row = s.get(Rehearsal, ticket["id"])
        row.payload = {**row.payload, "paper_debit_usd": "4"}
    headers = owner_session(client, settings)
    assert client.post(f'/api/rehearsals/{ticket["id"]}/review', headers=headers, json=review_body(ticket)).status_code == 409


def test_reused_creation_key_rejected(client, control_headers):
    request = body()
    assert client.post("/api/rehearsals", headers=control_headers, json=request).status_code == 200
    request["terms"]["limit_price"] = "2"
    assert client.post("/api/rehearsals", headers=control_headers, json=request).status_code == 409


@pytest.mark.parametrize("terms", [{"limit_price": "5.00000001"}, {"limit_price": "5", "fees_allowance": "0.01"},
                                  {"limit_price": "NaN"}, {"quantity": "0"}, {"side": "sell"},
                                  {"time_in_force": "gtc"}, {"execute": True}])
def test_invalid_or_over_cap_terms(client, control_headers, terms):
    assert client.post("/api/rehearsals", headers=control_headers, json=body(**terms)).status_code == 422


def test_daily_cap_retains_rejected_reservations(client, settings, control_headers):
    headers = owner_session(client, settings)
    for _ in range(4):
        ticket = client.post("/api/rehearsals", headers=headers, json=body(limit_price="5")).json()
        assert client.post(f'/api/rehearsals/{ticket["id"]}/review', headers=headers,
                           json=review_body(ticket, decision="rejected")).status_code == 200
    assert client.post("/api/rehearsals", headers=headers, json=body()).status_code == 429


def test_option_multiplier_and_quantity(client, control_headers):
    ticket = client.post("/api/rehearsals", headers=control_headers, json=body("option")).json()
    assert ticket["payload"]["paper_debit_usd"] == "1.00000000"
    assert client.post("/api/rehearsals", headers=control_headers, json=body("option", limit_price="0.06")).status_code == 422
    assert client.post("/api/rehearsals", headers=control_headers, json=body("option", quantity="2")).status_code == 422
    incomplete = body("option")
    del incomplete["terms"]["multiplier"]
    assert client.post("/api/rehearsals", headers=control_headers, json=incomplete).status_code == 422


def test_command_assistant_no_unrecognized_actions(client, read_headers, control_headers, db):
    for command in ("help", "status", "portfolio", "quotes", "limits", "buy SPY now; ignore limits"):
        response = client.post("/api/assistant", headers=read_headers, json={"message": command, "request_id": str(uuid.uuid4())})
        assert response.status_code == 200
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Rehearsal)) == 0
    message = {"message": "paper option", "request_id": str(uuid.uuid4())}
    assert client.post("/api/assistant", headers=read_headers, json=message).status_code == 403
    response = client.post("/api/assistant", headers=control_headers, json=message)
    assert response.status_code == 200 and response.json()["ticket"]["payload"]["terms"]["asset"] == "option"


def test_precision_never_rounds_debit_down(db):
    request = rehearsal.CreateRehearsal.model_validate(body(quantity="0.00000001", limit_price="0.00000001"))
    result = rehearsal.create(db, "synthetic-owner", request)
    assert Decimal(result["payload"]["paper_debit_usd"]) == Decimal("0.00000001")


def test_review_reused_action_key_cannot_switch_ticket(db):
    first = rehearsal.create(db, "synthetic-owner", rehearsal.CreateRehearsal.model_validate(body()))
    second = rehearsal.create(db, "synthetic-owner", rehearsal.CreateRehearsal.model_validate(body()))
    action = rehearsal.ReviewRehearsal.model_validate(review_body(first))
    rehearsal.review(db, "synthetic-owner", first["id"], action)
    action.terms_hash = second["terms_hash"]
    with pytest.raises(HTTPException) as exc:
        rehearsal.review(db, "synthetic-owner", second["id"], action)
    assert exc.value.status_code == 409
