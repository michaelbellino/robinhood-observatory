from datetime import timedelta
from uuid import uuid4
from types import SimpleNamespace
import json
import socket
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select, func
from observatory import push
from observatory.db import Event, PushDevice, PushDelivery, now
from tests.test_rehearsal import owner_session


def subscription():
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return push.Subscription(endpoint="https://web.push.apple.com/Q/test-only-token", keys={"p256dh": push.b64(key), "auth": push.b64(b"0" * 16)})


def ready(db, settings, vault):
    public_key = push.application_key(db, vault)
    assert public_key == push.application_key(db, vault)
    device = push.subscribe(db, vault, settings.owner_subject, subscription())
    event = push.test_event(db, settings.owner_subject)
    return device, event


async def test_encrypted_subscription_bounded_payload_and_dedup(db, settings, vault):
    device, event = ready(db, settings, vault)
    calls = []
    def sender(sub, keys, payload, origin, ttl):
        calls.append(payload)
        assert "private" in keys and ttl <= 300
        assert sub["endpoint"] == subscription().endpoint
        assert payload["url"] == "/trades"
        return SimpleNamespace(status_code=201)
    await push.dispatch(db, settings, vault, sender)
    await push.dispatch(db, settings, vault, sender)
    assert len(calls) == 1
    assert "TEST" not in json.dumps(calls) and "account_number" not in json.dumps(calls)
    with db.session() as s:
        row = s.get(PushDevice, device["id"])
        assert "test-only-token" not in row.subscription_cipher
        delivery = s.scalar(select(PushDelivery))
        assert delivery.state == "accepted_by_push_service"
    assert push.delivery_status(db)["provider_acceptance_is_device_receipt"] is False


async def test_backoff_expiry_and_device_removal(db, settings, vault):
    device, event = ready(db, settings, vault)
    await push.dispatch(db, settings, vault, lambda *args: SimpleNamespace(status_code=503))
    with db.session() as s:
        row = s.scalar(select(PushDelivery))
        assert row.state == "retry" and row.attempts == 1
        row.next_attempt = now()-timedelta(seconds=1)
    await push.dispatch(db, settings, vault, lambda *args: SimpleNamespace(status_code=410))
    with db.session() as s:
        assert not s.get(PushDevice, device["id"]).active
        assert s.scalar(select(PushDelivery)).state == "device_expired"


async def test_no_old_event_flood_and_no_synthetic_trade_notification(db, settings, vault):
    with db.session() as s:
        s.add(Event(id=str(uuid4()), kind="order_change", payload={}, occurred_at=now()-timedelta(minutes=20)))
    ready(db, settings, vault)
    await push.dispatch(db, settings, vault, lambda *args: SimpleNamespace(status_code=201))
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(PushDelivery)) == 1


async def test_expired_trade_alert_suppressed(configured, settings, vault):
    from observatory.trade_review import create_owner_ticket, OwnerTicket
    from tests.test_trade_review import owner_body
    from observatory.db import TradeIdea
    push.application_key(configured, vault)
    push.subscribe(configured, vault, settings.owner_subject, subscription())
    idea = create_owner_ticket(configured, settings.owner_subject, OwnerTicket.model_validate(owner_body()))
    with configured.session() as s:
        s.get(TradeIdea, idea["id"]).expires_at = now()-timedelta(seconds=1)
    def no_send(*args):
        pytest.fail("Expired idea must not notify")
    await push.dispatch(configured, settings, vault, no_send)
    with configured.session() as s:
        assert s.scalar(select(PushDelivery)).state == "expired"


@pytest.mark.parametrize("url", ["http://web.push.apple.com/x", "https://localhost/x", "https://127.0.0.1/x", "https://web.push.apple.com.evil.test/x", "https://user:pass@web.push.apple.com/x", "https://web.push.apple.com:444/x", "https://evil.test/x"])
def test_push_endpoints_fail_closed(url):
    with pytest.raises(ValueError):
        push.validate_endpoint(url)


def test_dns_private_address_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="Non-public"):
        push.PinnedPushSession().post(subscription().endpoint, b"x", {})


def test_real_encryption_transport_receives_ciphertext(monkeypatch, db, settings, vault):
    ready(db, settings, vault)
    with db.session() as s:
        keys = vault.get(s, "web_push_vapid")
    captured = []
    def post(self, endpoint, data, headers, **kw):
        captured.append(data)
        assert "authorization" in {x.lower() for x in headers}
        assert b"synthetic notice" not in data
        return SimpleNamespace(status_code=201, text="", headers={}, reason="Created")
    monkeypatch.setattr(push.PinnedPushSession, "post", post)
    push.send(subscription().model_dump(), keys, {"body": "synthetic notice"}, "https://fixture.example", 60)
    assert len(captured) == 1 and isinstance(captured[0], bytes)


def test_device_setup_requires_owner_session(client, settings, control_headers):
    assert client.post("/api/notifications/key", headers=control_headers).status_code == 403
    headers = owner_session(client, settings)
    assert client.post("/api/notifications/key").status_code == 403
    result = client.post("/api/notifications/key", headers=headers)
    assert result.status_code == 200 and result.json()["public_key"]
    assert client.post("/api/notifications/subscribe", headers=headers, json=subscription().model_dump()).status_code == 200
    assert "endpoint" not in client.get("/api/notifications").text

