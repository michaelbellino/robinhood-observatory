"""End-to-end PAPER review with an in-memory push provider; no live broker I/O."""
import base64
import json
import socket
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from itsdangerous import TimestampSigner
from sqlalchemy import select

from observatory import push, rehearsal
from observatory.broker import ReadOnlyBroker
from observatory.manual_orders import OwnerOrderBroker
from observatory.db import Event, PushDelivery, Rehearsal, now


def owner_headers(client, settings):
    encoded = base64.b64encode(json.dumps({"sub": settings.owner_subject, "csrf": "paper-csrf"}).encode())
    client.cookies.set("observatory_session", TimestampSigner(settings.session_secret).sign(encoded).decode())
    return {"X-CSRF-Token": "paper-csrf", "Origin": settings.public_url}


def add_device(db, settings, vault):
    pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    push.application_key(db, vault)
    push.subscribe(db, vault, settings.owner_subject, push.Subscription(
        endpoint="https://web.push.apple.com/Q/synthetic-paper-only",
        keys={"p256dh": push.b64(pub), "auth": push.b64(b"0" * 16)},
    ))


def paper_request():
    return {"request_id": str(uuid4()), "terms": rehearsal.fixture("option").model_dump(mode="json")}


@pytest.fixture
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Paper workflow attempted network or broker access")
    async def fail_async(*args, **kwargs):
        fail()
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail_async)
    monkeypatch.setattr(httpx.AsyncClient, "stream", fail)
    monkeypatch.setattr(ReadOnlyBroker, "call", fail_async)
    monkeypatch.setattr(OwnerOrderBroker, "call", fail_async)


@pytest.mark.parametrize("decision,expected", [("reviewed", "paper_reviewed"), ("rejected", "paper_rejected")])
async def test_paper_notification_and_owner_decision(client, db, settings, vault, control_headers, no_network, decision, expected):
    add_device(db, settings, vault)
    request = paper_request()
    response = client.post("/api/rehearsals", headers=control_headers, json=request)
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert ticket["status"] == "awaiting_paper_review" and ticket["sent_to_broker"] is False
    assert client.post("/api/rehearsals", headers=control_headers, json=request).json()["replayed"] is True

    sent = []
    def provider(subscription, keys, payload, origin, ttl):
        sent.append(payload)
        return SimpleNamespace(status_code=201)
    await push.dispatch(db, settings, vault, provider)
    await push.dispatch(db, settings, vault, provider)
    assert len(sent) == 1
    assert "paper" in sent[0]["title"].lower()
    assert sent[0]["url"] == "/workbench#" + ticket["id"]
    assert "TEST" not in json.dumps(sent[0]) and "0.01" not in json.dumps(sent[0])
    with db.session() as session:
        event = session.scalar(select(Event).where(Event.kind == "paper_rehearsal"))
        assert event is not None
        delivery = session.scalar(select(PushDelivery).where(PushDelivery.event_id == event.id))
        assert delivery.state == "accepted_by_push_service"
    assert push.delivery_status(db)["provider_acceptance_is_device_receipt"] is False

    headers = owner_headers(client, settings)
    body = {"request_id": str(uuid4()), "terms_hash": ticket["terms_hash"], "decision": decision,
            "acknowledge_paper_only": True}
    result = client.post(f'/api/rehearsals/{ticket["id"]}/review', headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == expected
    assert result.json()["sent_to_broker"] is False and result.json()["fill_status"] == "unfilled"
    assert client.post(f'/api/rehearsals/{ticket["id"]}/review', headers=headers, json=body).json()["replayed"] is True


async def test_paper_notification_failure_then_expiry_suppresses_retry(client, db, settings, vault, control_headers, no_network):
    add_device(db, settings, vault)
    ticket = client.post("/api/rehearsals", headers=control_headers, json=paper_request()).json()
    await push.dispatch(db, settings, vault, lambda *args: SimpleNamespace(status_code=503))
    with db.session() as session:
        delivery = session.scalar(select(PushDelivery))
        assert delivery.state == "retry"
        delivery.next_attempt = now() - timedelta(seconds=1)
        session.get(Rehearsal, ticket["id"]).expires_at = now() - timedelta(seconds=1)
    def no_send(*args):
        pytest.fail("An expired paper proposal must not be notified")
    await push.dispatch(db, settings, vault, no_send)
    with db.session() as session:
        assert session.scalar(select(PushDelivery)).state == "expired"
