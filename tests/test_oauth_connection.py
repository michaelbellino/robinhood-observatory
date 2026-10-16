"""Protocol and error-path tests use synthetic OAuth data only."""

import base64
import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from observatory.broker import ENDPOINT, AuthorizationExpired
from observatory.db import Audit, OAuthFlow, State, now
from observatory.oauth import (
    AUTH_METADATA,
    AUTHORIZATION,
    REGISTRATION,
    RESOURCE_METADATA,
    TOKEN,
    OAuthProblem,
    RobinhoodOAuth,
)
from observatory.security import digest


@pytest.fixture
def broker_metadata(respx_mock):
    respx_mock.get(RESOURCE_METADATA).respond(200, json={"resource": ENDPOINT})
    respx_mock.get(AUTH_METADATA).respond(
        200,
        json={
            "issuer": ENDPOINT,
            "authorization_endpoint": AUTHORIZATION,
            "token_endpoint": TOKEN,
            "registration_endpoint": REGISTRATION,
            "code_challenge_methods_supported": ["S256"],
        },
    )


@pytest.fixture
def registration(broker_metadata, respx_mock, settings):
    return respx_mock.post(REGISTRATION).respond(
        200,
        json={
            # Mirrors the broker returning a generic name; this is not proof of consent.
            "client_name": "Robinhood Trading",
            "client_id": "synthetic-client",
            "redirect_uris": [settings.public_url + "/oauth/robinhood/callback"],
            "token_endpoint_auth_method": "none",
        },
    )


async def start(db, vault, settings):
    url = await RobinhoodOAuth(db, vault, settings).begin(settings.owner_subject)
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def test_full_pkce_flow_recovers_after_restart(db, vault, settings, registration, respx_mock):
    params = await start(db, vault, settings)
    assert params["scope"] == "internal"
    assert params["resource"] == ENDPOINT
    assert params["response_type"] == "code"
    assert params["code_challenge_method"] == "S256"
    assert len(params["code_challenge"]) == 43
    with db.session() as session:
        flow = session.get(OAuthFlow, digest(params["state"]))
        payload = vault.decrypt(flow.payload_cipher)
        assert payload["redirect"] == params["redirect_uri"]
        assert (
            base64.urlsafe_b64encode(hashlib.sha256(payload["verifier"].encode()).digest())
            .decode()
            .rstrip("=")
            == params["code_challenge"]
        )
        assert payload["verifier"] not in flow.payload_cipher
    exchange = respx_mock.post(TOKEN).respond(
        200,
        json={
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    # New instance represents process restart: all transaction data is durable.
    restarted = RobinhoodOAuth(db, vault, settings)
    await restarted.callback(settings.owner_subject, params["state"], "synthetic-code", ENDPOINT)
    sent = parse_qs(exchange.calls.last.request.content.decode())
    assert sent["code_verifier"] == [payload["verifier"]]
    assert sent["redirect_uri"] == [params["redirect_uri"]]
    assert sent["client_id"] == [params["client_id"]]
    assert sent["resource"] == [ENDPOINT]
    assert await restarted.access_token() == "synthetic-access"
    with pytest.raises(OAuthProblem, match="expired or is invalid"):
        await restarted.callback(settings.owner_subject, params["state"], "synthetic-code", ENDPOINT)
    assert exchange.call_count == 1
    safe = str(restarted.diagnostics(settings.owner_subject))
    for secret in (
        params["state"],
        payload["verifier"],
        "synthetic-code",
        "synthetic-access",
        "synthetic-refresh",
    ):
        assert secret not in safe


async def test_attempt_client_survives_registration_change(db, vault, settings, registration, respx_mock):
    params = await start(db, vault, settings)
    with db.session() as session:
        vault.put(session, "robinhood_client", {"client_id": "new-synthetic-client"})
    exchange = respx_mock.post(TOKEN).respond(
        200,
        json={
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "token_type": "Bearer",
            "expires_in": 1,
        },
    )
    oauth = RobinhoodOAuth(db, vault, settings)
    await oauth.callback(settings.owner_subject, params["state"], "synthetic-code", ENDPOINT)
    await oauth.access_token()  # Refresh must use the credential's original client too.
    assert exchange.call_count == 2
    for call in exchange.calls:
        assert parse_qs(call.request.content.decode())["client_id"] == ["synthetic-client"]


@pytest.mark.parametrize(
    "issuer,error,code,expected",
    [
        (None, None, "synthetic-code", "issuer_mismatch"),
        ("https://attacker.invalid", None, "synthetic-code", "issuer_mismatch"),
        (ENDPOINT, "access_denied", None, "consent_denied"),
        (ENDPOINT, "sensitive-provider-text", None, "provider_error"),
        (ENDPOINT, "access_denied", "synthetic-code", "invalid_callback"),
        (ENDPOINT, None, None, "invalid_callback"),
    ],
)
async def test_callback_failures_are_specific_and_single_use(
    db, vault, settings, registration, respx_mock, caplog, issuer, error, code, expected
):
    params = await start(db, vault, settings)
    exchange = respx_mock.post(TOKEN)
    oauth = RobinhoodOAuth(db, vault, settings)
    with pytest.raises(OAuthProblem) as caught:
        await oauth.callback(settings.owner_subject, params["state"], code, issuer, error)
    assert caught.value.code == expected
    assert not exchange.called
    with db.session() as session:
        assert session.get(OAuthFlow, digest(params["state"])) is None
    assert "sensitive-provider-text" not in caplog.text
    assert "synthetic-code" not in str(oauth.diagnostics(settings.owner_subject))


async def test_wrong_owner_does_not_consume_flow(db, vault, settings, registration):
    params = await start(db, vault, settings)
    with pytest.raises(OAuthProblem):
        await RobinhoodOAuth(db, vault, settings).callback(
            "other-owner", params["state"], "synthetic", ENDPOINT
        )
    with db.session() as session:
        assert session.get(OAuthFlow, digest(params["state"])) is not None


async def test_expired_state_does_not_exchange(db, vault, settings, registration, respx_mock):
    params = await start(db, vault, settings)
    with db.session() as session:
        session.get(OAuthFlow, digest(params["state"])).expires_at = now() - timedelta(seconds=1)
    exchange = respx_mock.post(TOKEN)
    with pytest.raises(OAuthProblem):
        await RobinhoodOAuth(db, vault, settings).callback(
            settings.owner_subject, params["state"], "synthetic", ENDPOINT
        )
    assert not exchange.called


def test_api_error_callback_no_secret_echo(client, control_headers, registration, respx_mock, caplog, db):
    url = client.post("/api/robinhood/connect", headers=control_headers, json={}).json()["authorization_url"]
    params = parse_qs(urlsplit(url).query)
    response = client.get(
        "/oauth/robinhood/callback",
        headers=control_headers,
        params={
            "state": params["state"][0],
            "iss": ENDPOINT,
            "error": "access_denied",
            "error_description": "synthetic-sensitive-provider-text",
        },
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "consent_denied"
    assert "synthetic-sensitive-provider-text" not in response.text
    # HTTPX test-client request logging includes URLs; application logs must never do so.
    app_logs = " ".join(r.message for r in caplog.records if r.name == "observatory")
    assert "synthetic-sensitive-provider-text" not in app_logs
    with db.session() as session:
        assert session.get(State, 1).paused
        assert "synthetic-sensitive-provider-text" not in str(
            [a.details for a in session.scalars(select(Audit))]
        )


def test_api_missing_parameters_not_fastapi_422(client, control_headers):
    response = client.get("/oauth/robinhood/callback", headers=control_headers)
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_state"


def test_duplicate_callback_parameters_rejected(client, control_headers):
    response = client.get(
        "/oauth/robinhood/callback?state=a&state=b&code=synthetic-secret", headers=control_headers
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_callback"
    assert "synthetic-secret" not in response.text


def test_diagnostics_are_owner_control_only(client, read_headers, control_headers):
    assert client.get("/api/robinhood/diagnostics").status_code == 401
    assert client.get("/api/robinhood/diagnostics", headers=read_headers).status_code == 403
    result = client.get("/api/robinhood/diagnostics", headers=control_headers)
    assert result.status_code == 200
    assert result.json()["registration_proves_redirect_accepted_at_consent"] is False


def test_api_success_stays_paused_until_account_selected(
    client, control_headers, registration, respx_mock, db
):
    url = client.post("/api/robinhood/connect", headers=control_headers, json={}).json()["authorization_url"]
    params = parse_qs(urlsplit(url).query)
    respx_mock.post(TOKEN).respond(
        200, json={"access_token": "synthetic-access", "token_type": "Bearer", "expires_in": 3600}
    )
    result = client.get(
        "/oauth/robinhood/callback",
        headers=control_headers,
        params={
            "state": params["state"][0],
            "iss": ENDPOINT,
            "code": "synthetic-code",
        },
        follow_redirects=False,
    )
    assert result.status_code == 303
    assert result.headers["location"] == "/"
    with db.session() as session:
        state = session.get(State, 1)
        assert state.paused and not state.account_verified and state.status == "select_account"


@pytest.mark.parametrize("body", [{}, {"access_token": "synthetic", "token_type": "Bearer"}, ["invalid"]])
async def test_malformed_token_never_saved(db, vault, settings, registration, respx_mock, body):
    params = await start(db, vault, settings)
    respx_mock.post(TOKEN).respond(200, json=body)
    oauth = RobinhoodOAuth(db, vault, settings)
    with pytest.raises(OAuthProblem) as caught:
        await oauth.callback(settings.owner_subject, params["state"], "synthetic-code", ENDPOINT)
    assert caught.value.code == "token_invalid"
    with pytest.raises(AuthorizationExpired):
        await oauth.access_token()


async def test_incompatible_registration_rejected(db, vault, settings, registration):
    registration.respond(
        200,
        json={
            "client_id": "synthetic",
            "redirect_uris": ["https://attacker.invalid"],
            "token_endpoint_auth_method": "none",
        },
    )
    with pytest.raises(OAuthProblem) as caught:
        await start(db, vault, settings)
    assert caught.value.code == "registration_invalid"
    with db.session() as session:
        assert vault.get(session, "robinhood_client") is None


@pytest.mark.parametrize(
    "failure,expected", [("timeout", "exchange_unavailable"), ("400", "exchange_failed")]
)
async def test_exchange_failure_never_retries_used_code(
    db, vault, settings, registration, respx_mock, failure, expected
):
    import httpx

    params = await start(db, vault, settings)
    route = respx_mock.post(TOKEN)
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("synthetic-private-provider-exception"))
    else:
        route.respond(400, json={"error_description": "synthetic-private-provider-exception"})
    oauth = RobinhoodOAuth(db, vault, settings)
    with pytest.raises(OAuthProblem) as caught:
        await oauth.callback(settings.owner_subject, params["state"], "synthetic-code", ENDPOINT)
    assert caught.value.code == expected
    with pytest.raises(OAuthProblem) as retry:
        await oauth.callback(settings.owner_subject, params["state"], "synthetic-code", ENDPOINT)
    assert retry.value.code == "invalid_state"
    assert route.call_count == 1
    assert not oauth.diagnostics(settings.owner_subject)["credentials_saved"]
