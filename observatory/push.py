"""Encrypted Web Push with a bounded durable outbox. No trade data on lock screens."""
import asyncio
import base64
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from pydantic import Field, model_validator
from py_vapid import Vapid
from pywebpush import webpush, WebPushException
from requests import Response
from sqlalchemy import select, func
from .db import State, Event, TradeIdea, Rehearsal, PushDevice, PushDelivery, Audit, aware, now
from .domain import StrictModel

PUSH_HOSTS = {"web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com"}


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def validate_endpoint(endpoint):
    url = urlsplit(endpoint)
    host = url.hostname or ""
    if (url.scheme != "https" or url.username or url.password or url.fragment or url.port not in (None, 443)
            or not (host in PUSH_HOSTS or host.endswith(".notify.windows.com"))):
        raise ValueError("Unsupported push service")
    return url


class Subscription(StrictModel):
    endpoint: str = Field(min_length=10, max_length=2048)
    keys: dict[str, str]
    expirationTime: int | None = None

    @model_validator(mode="after")
    def valid(self):
        validate_endpoint(self.endpoint)
        if set(self.keys) != {"p256dh", "auth"} or len(self.keys["p256dh"]) > 100 or len(self.keys["auth"]) > 30:
            raise ValueError("Invalid push subscription keys")
        key = unb64(self.keys["p256dh"])
        if len(key) != 65 or len(unb64(self.keys["auth"])) != 16:
            raise ValueError("Invalid push subscription key lengths")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), key)
        return self


class PinnedPushSession:
    def post(self, endpoint, data, headers, timeout=10, **kwargs):
        url = validate_endpoint(endpoint)
        addresses = socket.getaddrinfo(url.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ValueError("Non-public push service address")
        address = addresses[0][4][0]
        # Pin the validated address at connect time; preserve original SNI and certificate verification.
        connection = http.client.HTTPSConnection(url.hostname, 443, timeout=min(timeout or 10, 10))
        raw = socket.create_connection((address, 443), timeout=min(timeout or 10, 10))
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=url.hostname)
            path = url.path or "/"
            if url.query:
                path += "?" + url.query
            connection.request("POST", path, body=data, headers={str(k): str(v) for k, v in headers.items()})
            received = connection.getresponse()
            body = received.read(8193)
            if len(body) > 8192:
                raise ValueError("Push service response too large")
            response = Response()
            response.status_code, response.reason, response._content = received.status, received.reason, body
            response.headers.update(dict(received.getheaders()))
            return response  # Redirects are never followed.
        finally:
            connection.close()
            raw.close()


def application_key(db, vault):
    with db.session() as s:
        s.scalar(select(State).where(State.id == 1).with_for_update())
        keys = vault.get(s, "web_push_vapid")
        if not keys:
            vapid = Vapid()
            vapid.generate_keys()
            keys = {"private": vapid.private_pem().decode(),
                    "public": b64(vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                               serialization.PublicFormat.UncompressedPoint))}
            vault.put(s, "web_push_vapid", keys)
        return keys["public"]


def subscribe(db, vault, owner, body):
    ident = hashlib.sha256(body.endpoint.encode()).hexdigest()
    with db.session() as s:
        s.scalar(select(State).where(State.id == 1).with_for_update())
        row = s.get(PushDevice, ident)
        if not row:
            count = s.scalar(select(func.count()).select_from(PushDevice).where(PushDevice.active.is_(True)))
            if count >= 5:
                raise HTTPException(409, "Five device limit; disable an old device first")
            row = PushDevice(id=ident, owner=owner, created_at=now())
            s.add(row)
        elif not row.active:
            row.created_at = now()
        row.subscription_cipher, row.active = vault.encrypt(body.model_dump(mode="json")), True
        s.add(Audit(id=str(uuid4()), actor=owner, action="push_device_enabled", details={"device_id": ident}))
    return {"id": ident, "active": True}


def disable(db, owner, ident):
    with db.session() as s:
        row = s.get(PushDevice, ident)
        if row and row.owner == owner:
            row.active = False
            s.add(Audit(id=str(uuid4()), actor=owner, action="push_device_disabled", details={"device_id": ident}))
    return {"active": False}


def test_event(db, owner):
    with db.session() as s:
        s.scalar(select(State).where(State.id == 1).with_for_update())
        recent = s.scalar(select(Event).where(Event.kind == "notification_test", Event.occurred_at > now()-timedelta(minutes=1)))
        if recent:
            return {"event_id": recent.id, "replayed": True}
        if not s.scalar(select(PushDevice.id).where(PushDevice.owner == owner, PushDevice.active.is_(True))):
            raise HTTPException(409, "Enable notifications on a device first")
        ident = str(uuid4())
        s.add(Event(id=ident, kind="notification_test", payload={"test": True}))
        return {"event_id": ident, "queued": True}


def delivery_status(db):
    with db.session() as s:
        devices = [{"id": x.id, "active": x.active, "created_at": aware(x.created_at)}
                   for x in s.scalars(select(PushDevice).order_by(PushDevice.created_at.desc()).limit(20))]
        deliveries = [{"event_id": x.event_id, "state": x.state, "attempts": x.attempts,
                       "updated_at": aware(x.updated_at)} for x in s.scalars(select(PushDelivery)
                       .order_by(PushDelivery.updated_at.desc()).limit(15))]
        return {"devices": devices, "deliveries": deliveries, "chatgpt_events": "not_connected",
                "provider_acceptance_is_device_receipt": False}


def send(subscription, keys, payload, origin, ttl):
    return webpush(subscription, json.dumps(payload), vapid_private_key=Vapid.from_pem(keys["private"].encode()),
                   vapid_claims={"sub": origin}, ttl=ttl, timeout=10, requests_session=PinnedPushSession())


async def dispatch(db, settings, vault, sender=send):
    with db.session() as s:
        s.scalar(select(State).where(State.id == 1).with_for_update())
        keys = vault.get(s, "web_push_vapid")
        if not keys:
            return
        devices = s.scalars(select(PushDevice).where(PushDevice.active.is_(True), PushDevice.owner == settings.owner_subject)).all()
        if not devices:
            return
        events = s.scalars(select(Event).where(Event.occurred_at > now()-timedelta(minutes=15),
                           Event.delivery_state != "baseline").order_by(Event.occurred_at.desc()).limit(30)).all()
        for event in events:
            for device in devices:
                if aware(event.occurred_at) < aware(device.created_at):
                    continue
                ident = hashlib.sha256(f"{event.id}:{device.id}".encode()).hexdigest()
                if not s.get(PushDelivery, ident):
                    s.add(PushDelivery(id=ident, event_id=event.id, device_id=device.id))
        # Recover a crash after claiming. Event tags replace duplicates on device; delivery is at-least-once.
        for row in s.scalars(select(PushDelivery).where(PushDelivery.state == "sending",
                            PushDelivery.updated_at < now()-timedelta(minutes=2)).with_for_update(skip_locked=True)):
            row.state = "retry"
    for _ in range(5):
        with db.session() as s:
            row = s.scalar(select(PushDelivery).where(PushDelivery.state.in_(["pending", "retry"]),
                           PushDelivery.next_attempt <= now()).order_by(PushDelivery.next_attempt)
                           .with_for_update(skip_locked=True).limit(1))
            if not row:
                break
            event, device = s.get(Event, row.event_id), s.get(PushDevice, row.device_id)
            if not event or not device or not device.active or device.owner != settings.owner_subject or aware(event.occurred_at) < now()-timedelta(minutes=15):
                row.state = "expired"
                continue
            ttl = 300
            path = "/trades"
            title, message = "Observatory update", "An account or monitoring update is ready to review."
            if event.kind == "trade_idea":
                idea = s.get(TradeIdea, event.payload.get("idea_id"))
                if not idea or idea.status != "awaiting_review" or aware(idea.expires_at) <= now():
                    row.state = "expired"
                    continue
                ttl = max(1, min(ttl, int((aware(idea.expires_at)-now()).total_seconds())))
                path += "#" + idea.id
                title, message = "Trade idea ready", "Review the proposal and current broker preview before deciding."
            elif event.kind == "paper_rehearsal":
                paper = s.get(Rehearsal, event.payload.get("rehearsal_id"))
                if not paper or paper.status != "awaiting_paper_review" or aware(paper.expires_at) <= now():
                    row.state = "expired"
                    continue
                ttl = max(1, min(ttl, int((aware(paper.expires_at)-now()).total_seconds())))
                path = "/workbench#" + paper.id
                title, message = "PAPER proposal ready", "Review this synthetic paper test. No brokerage order was created."
            elif event.kind == "trade_order_status":
                idea = s.get(TradeIdea, event.payload.get("idea_id"))
                if not idea:
                    row.state = "expired"
                    continue
                path += "#" + idea.id
                title, message = "Order status updated", "Open Observatory to review the broker's reported status."
            elif event.kind == "notification_test":
                title, message = "Observatory notification test", "Phone notifications are working. No order was created or submitted."
            payload = {"id": event.id, "title": title, "body": message, "url": path,
                       "expires_at": (now()+timedelta(seconds=ttl)).isoformat()}
            subscription = vault.decrypt(device.subscription_cipher)
            ident = row.id
            row.state, row.updated_at, row.attempts = "sending", now(), row.attempts+1
        try:
            response = await asyncio.to_thread(sender, subscription, keys, payload, settings.public_url, ttl)
            code = response.status_code
        except WebPushException as exc:
            code = exc.response.status_code if exc.response is not None else 0
        except Exception:
            code = 0  # No exception body, endpoint token, or key in logs.
        with db.session() as s:
            row = s.get(PushDelivery, ident)
            row.updated_at = now()
            if 200 <= code < 300:
                row.state = "accepted_by_push_service"
            elif code in (404, 410):
                row.state = "device_expired"
                s.get(PushDevice, row.device_id).active = False
            elif (code == 0 or code == 429 or code >= 500) and row.attempts < 4:
                row.state = "retry"
                row.next_attempt = now()+timedelta(seconds=30 * 2**row.attempts)
            else:
                row.state = "failed"


