"""Synthetic OAuth, real loopback HTTP, actual app authentication/vault boundaries."""

import base64
import hashlib
import threading
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from observatory.db import Audit, OAuthFlow, Secret, State, now
from observatory.oauth import RobinhoodOAuth
from observatory.security import digest
from scripts.connect_robinhood import (
    AUTH_METADATA,
    AUTHORIZE,
    REGISTER,
    RESOURCE,
    RESOURCE_METADATA,
    TOKEN,
    EnrollmentError,
    LoopbackServer,
    NativeEnrollment,
    check_app,
    handler_for,
)


def deposit(proof="synthetic-helper-proof"):
    return {
        "proof": proof,
        "client_id": "synthetic-native-client",
        "redirect_uri": "http://127.0.0.1:45678/callback",
        "token": {
            "access_token": "synthetic-native-access",
            "refresh_token": "synthetic-native-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    }


def ticket_for(client, control_headers, proof="synthetic-helper-proof", port=45678):
    r = client.post(
        "/api/robinhood/local-enrollment",
        headers=control_headers,
        json={"port": port, "challenge": digest(proof)},
    )
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["ticket"]}


def test_authorization_and_capability_boundaries(client, control_headers, read_headers):
    args = {"port": 45678, "challenge": digest("synthetic-helper-proof")}
    assert client.post("/api/robinhood/local-enrollment", json=args).status_code == 401
    assert client.post("/api/robinhood/local-enrollment", json=args, headers=read_headers).status_code == 403
    ticket = ticket_for(client, control_headers)
    for path in ("/api/status", "/api/robinhood/diagnostics", "/mcp/read/", "/mcp/control/"):
        assert client.get(path, headers=ticket).status_code == 401
    assert (
        client.post("/api/pause", headers=ticket, json={"paused": False, "request_id": "fixture"}).status_code
        == 401
    )
    for headers in ({}, control_headers, read_headers):
        assert (
            client.post("/api/robinhood/local-complete", headers=headers, json=deposit()).status_code == 401
        )
    body = deposit("wrong-proof")
    assert client.post("/api/robinhood/local-complete", headers=ticket, json=body).status_code == 401


def test_one_time_deposit_encrypted_and_pause_survives_restart(
    client, db, vault, settings, control_headers, caplog
):
    ticket = ticket_for(client, control_headers)
    r = client.post("/api/robinhood/local-complete", headers=ticket, json=deposit())
    assert r.status_code == 200 and r.json()["monitoring"] == "paused"
    # A retry acknowledges the prior result; it cannot overwrite the first credential.
    replay = deposit()
    replay["token"]["access_token"] = "must-never-replace"
    assert client.post("/api/robinhood/local-complete", headers=ticket, json=replay).status_code == 200
    with db.session() as s:
        state = s.get(State, 1)
        assert state.paused and not state.account_verified and not state.account_cipher
        saved = vault.get(s, "robinhood_tokens")
        assert saved["access_token"] == "synthetic-native-access"
        assert saved["client_id"] == "synthetic-native-client"
        assert "synthetic-native" not in s.get(Secret, "robinhood_tokens").cipher
        assert len(s.scalars(select(Audit).where(Audit.action == "local_enrollment_completed")).all()) == 1
    diagnostics = RobinhoodOAuth(db, vault, settings).diagnostics(settings.owner_subject)
    assert diagnostics["credentials_saved"] and diagnostics["enrollment_method"] == "native_loopback"
    assert "synthetic-native-access" not in str(diagnostics) + r.text + caplog.text
    assert (
        client.post(
            "/api/robinhood/local-enrollment",
            headers=control_headers,
            json={"port": 45678, "challenge": digest("another")},
        ).status_code
        == 409
    )


def test_expired_replaced_and_wrong_redirect_rejected(client, db, control_headers):
    old = ticket_for(client, control_headers)
    new = ticket_for(client, control_headers)
    assert client.post("/api/robinhood/local-complete", headers=old, json=deposit()).status_code == 401
    body = deposit()
    body["redirect_uri"] = "https://attacker.invalid/callback"
    assert client.post("/api/robinhood/local-complete", headers=new, json=body).status_code == 400
    with db.session() as s:
        for flow in s.scalars(select(OAuthFlow)).all():
            flow.expires_at = now() - timedelta(seconds=1)
    assert client.post("/api/robinhood/local-complete", headers=new, json=deposit()).status_code == 401


@pytest.mark.parametrize(
    "field,value",
    [
        ("refresh_token", None),
        ("token_type", 12),
        ("expires_in", -1),
        ("expires_in", True),
        ("expires_in", 10**12),
    ],
)
def test_bad_credentials_never_persist(client, db, control_headers, field, value):
    ticket = ticket_for(client, control_headers)
    body = deposit()
    body["token"][field] = value
    assert client.post("/api/robinhood/local-complete", headers=ticket, json=body).status_code == 400
    with db.session() as s:
        assert s.get(Secret, "robinhood_tokens") is None
        assert s.get(State, 1).paused


def test_deposit_payload_size_and_local_page_validation(client, control_headers):
    assert client.post("/api/robinhood/local-complete", content=b"x" * 40001).status_code == 413
    assert client.get("/local-connect?port=0&challenge=" + "f" * 64).status_code == 400
    assert client.get("/local-connect?port=45678&challenge=" + "f" * 64).status_code == 200


def fake_broker(calls):
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url == RESOURCE_METADATA:
            return {"resource": RESOURCE}
        if url == AUTH_METADATA:
            return {
                "issuer": RESOURCE,
                "authorization_endpoint": AUTHORIZE,
                "token_endpoint": TOKEN,
                "registration_endpoint": REGISTER,
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "authorization_response_iss_parameter_supported": True,
            }
        if url == REGISTER:
            return {
                "client_id": "synthetic-native-client",
                "redirect_uris": kwargs["data"]["redirect_uris"],
                "token_endpoint_auth_method": "none",
            }
        if url == TOKEN:
            return deposit()["token"]
        return {"connected": True, "monitoring": "paused"}

    return request


def test_native_pkce_and_real_loopback_handoff_to_app(client, db, vault, control_headers, caplog):
    calls = []
    broker = fake_broker(calls)

    def request(url, **kwargs):
        if url.endswith("/api/robinhood/local-complete"):
            r = client.post(
                "/api/robinhood/local-complete",
                json=kwargs["data"],
                headers={"Authorization": "Bearer " + kwargs["bearer"]},
            )
            assert r.status_code == 200
            return r.json()
        return broker(url, **kwargs)

    native = NativeEnrollment("https://observatory.example", request=request)
    with LoopbackServer(("127.0.0.1", 0), handler_for(native)) as server:
        landing = native.prepare(server.server_port)
        assert parse_qs(urlsplit(landing).query)["challenge"] == [native.challenge]
        ticket = ticket_for(client, control_headers, native.proof, server.server_port)["Authorization"][7:]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(trust_env=False) as browser:
                assert (
                    browser.get(native.origin + "/enroll", headers={"Host": "attacker.invalid"}).status_code
                    == 400
                )
                body = {"ticket": ticket, "challenge": native.challenge, "csrf": native.csrf}
                assert (
                    browser.post(
                        native.origin + "/enroll", json=body, headers={"Origin": "https://attacker.invalid"}
                    ).status_code
                    == 403
                )
                r = browser.post(native.origin + "/enroll", json=body, headers={"Origin": native.origin})
                assert r.status_code == 200
                params = parse_qs(urlsplit(r.json()["authorization_url"]).query)
                expected = (
                    base64.urlsafe_b64encode(hashlib.sha256(native.verifier.encode()).digest())
                    .rstrip(b"=")
                    .decode()
                )
                assert params["code_challenge"] == [expected]
                assert params["redirect_uri"] == [native.redirect]
                result = browser.get(
                    native.redirect, params={"state": native.state, "code": "synthetic-code", "iss": RESOURCE}
                )
                assert result.status_code == 200 and native.success
                assert (
                    browser.get(
                        native.redirect,
                        params={"state": native.state, "code": "synthetic-code", "iss": RESOURCE},
                    ).status_code
                    == 400
                )
            with db.session() as s:
                assert vault.get(s, "robinhood_tokens")["client_id"] == "synthetic-native-client"
                assert s.get(State, 1).paused
            assert len([c for c in calls if c[0] == TOKEN]) == 1
            # App's security logger never sees credentials (httpx test logging is not production).
            assert not [
                r for r in caplog.records if r.name == "observatory" and "synthetic-code" in r.message
            ]
        finally:
            server.shutdown()
            thread.join(timeout=2)


@pytest.mark.parametrize(
    "query",
    [
        {"state": ["wrong"], "code": ["synthetic-code"], "iss": [RESOURCE]},
        {"state": [], "code": ["synthetic-code"], "iss": [RESOURCE]},
        {"code": ["synthetic-code"]},
        {"code": ["synthetic-code"], "iss": ["https://attacker.invalid"]},
        {"code": ["one", "two"], "iss": [RESOURCE]},
        {"error": ["provider-secret-text"], "iss": [RESOURCE]},
        {"error": [""], "code": ["synthetic-code"], "iss": [RESOURCE]},
    ],
)
def test_native_rejects_invalid_callback_before_exchange(query):
    calls = []
    native = NativeEnrollment("https://observatory.example", fake_broker(calls))
    native.prepare(45678)
    native.accept_ticket({"ticket": "t" * 43, "challenge": native.challenge, "csrf": native.csrf})
    query.setdefault("state", [native.state])
    with pytest.raises(EnrollmentError) as caught:
        native.finish(query)
    assert not [c for c in calls if c[0] == TOKEN]
    assert "provider-secret-text" not in str(caught.value)


@pytest.mark.parametrize(
    "app",
    [
        "http://example.org",
        "https://user:secret@example.org",
        "https://example.org/path",
        "https://example.org/?token=secret",
    ],
)
def test_helper_rejects_insecure_or_ambiguous_app_destinations(app):
    with pytest.raises(EnrollmentError):
        check_app(app)


async def test_native_refresh_uses_durable_client_after_restart(
    client, db, vault, settings, control_headers, respx_mock
):
    ticket = ticket_for(client, control_headers)
    assert client.post("/api/robinhood/local-complete", json=deposit(), headers=ticket).status_code == 200
    with db.session() as s:
        token = vault.get(s, "robinhood_tokens")
        token["expires_at"] = now().isoformat()
        vault.put(s, "robinhood_tokens", token)
    refresh = respx_mock.post(TOKEN).respond(
        200, json={**deposit()["token"], "access_token": "synthetic-renewed"}
    )
    assert await RobinhoodOAuth(db, vault, settings).access_token() == "synthetic-renewed"
    sent = parse_qs(refresh.calls.last.request.content.decode())
    assert sent["client_id"] == ["synthetic-native-client"]
    assert sent["grant_type"] == ["refresh_token"]
    with db.session() as s:
        assert s.get(State, 1).paused
