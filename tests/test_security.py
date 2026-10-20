import uuid
import pytest
from observatory.broker import ReadOnlyBroker, ALLOWED
from observatory.db import Audit, State, Secret
from observatory.security import Vault
from sqlalchemy import select, func


@pytest.mark.parametrize(
    "path",
    [
        "/api/status",
        "/api/history/events",
        "/api/robinhood/accounts",
        "/api/session",
        "/mcp/read/",
        "/mcp/control/",
    ],
)
def test_anonymous_denied(client, path):
    assert client.get(path).status_code == 401


def test_malformed_bearer_denied(client):
    assert client.get("/api/status", headers={"Authorization": "Bearer invalid"}).status_code == 401


def test_read_cannot_control(client, read_headers):
    assert (
        client.post(
            "/api/pause", headers=read_headers, json={"paused": True, "request_id": str(uuid.uuid4())}
        ).status_code
        == 403
    )
    assert client.post("/mcp/control/", headers=read_headers, json={}).status_code == 403


def test_control_audited_idempotent(client, configured, control_headers, db):
    request = {"paused": True, "request_id": str(uuid.uuid4())}
    assert client.post("/api/pause", headers=control_headers, json=request).status_code == 200
    assert client.post("/api/pause", headers=control_headers, json=request).json()["replayed"]
    with db.session() as s:
        assert s.get(State, 1).paused
        assert s.scalar(select(func.count()).select_from(Audit)) == 1


def test_resume_requires_binding(client, control_headers):
    r = client.post(
        "/api/pause", headers=control_headers, json={"paused": False, "request_id": str(uuid.uuid4())}
    )
    assert r.status_code == 400


def test_no_broker_proxy(client, control_headers):
    assert client.post("/api/broker/place_order", headers=control_headers, json={}).status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "place_equity_order",
        "place_option_order",
        "cancel_equity_order",
        "exercise_option",
        "review_option_order",
        "create_alert",
        "mark_alerts_read",
    ],
)
def test_mutations_denied_even_if_discovered(name):
    b = ReadOnlyBroker("synthetic", "SYNTHETIC-ACCOUNT")
    b.schemas[name] = {"type": "object"}
    with pytest.raises(PermissionError):
        b.validate_call(name, {})
    assert name not in ALLOWED


def test_account_binding_enforced():
    b = ReadOnlyBroker("synthetic", "selected")
    b.schemas["get_portfolio"] = {"type": "object", "properties": {"account_number": {"type": "string"}}}
    with pytest.raises(PermissionError):
        b.validate_call("get_portfolio", {"account_number": "other"})


def test_user_wide_alerts_denied():
    b = ReadOnlyBroker("synthetic", "selected")
    b.schemas["get_alert_log"] = {"type": "object"}
    with pytest.raises(PermissionError):
        b.validate_call("get_alert_log", {})


def test_encrypted_at_rest(db, vault, settings):
    with db.session() as s:
        vault.put(s, "test", {"refresh_token": "synthetic-secret"})
    with db.session() as s:
        raw = s.get(Secret, "test").cipher
        assert "synthetic-secret" not in raw
        assert Vault(settings.encryption_keys).decrypt(raw)["refresh_token"] == "synthetic-secret"


def test_missing_auth_configuration_fails(settings):
    settings.owner_subject = ""
    with pytest.raises(ValueError):
        settings.validate()


def test_no_fixture_leak(client, read_headers):
    data = client.get("/api/status", headers=read_headers).json()
    assert data["snapshot"] is None
    assert data["data_age_seconds"] is None


def test_security_headers(client):
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_mcp_discovery_scoped(client, read_headers):
    headers = {**read_headers, "Accept": "application/json, text/event-stream"}
    response = client.post(
        "/mcp/read/", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert response.status_code == 200, response.text
    tools = response.json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"monitor_status", "monitor_history", "trade_review_inbox"}


def test_mcp_status_call(client, read_headers):
    headers = {**read_headers, "Accept": "application/json, text/event-stream"}
    response = client.post(
        "/mcp/read/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "monitor_status", "arguments": {}},
        },
    )
    assert response.status_code == 200
    assert not response.json()["result"].get("isError")


def test_watchlist_limit(client, control_headers):
    r = client.post(
        "/api/config", headers=control_headers, json={"watchlist": ["T" + str(n) for n in range(21)]}
    )
    assert r.status_code == 422


def test_mcp_dns_rebinding_denied(client, read_headers):
    r = client.post(
        "/mcp/read/",
        headers={**read_headers, "Host": "attacker.invalid", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code == 421


def test_sensitive_settings_not_in_repr(settings):
    settings.openai_key = "synthetic-sensitive-value"
    settings.database_url = "synthetic-database-private-value"
    assert "synthetic-sensitive-value" not in repr(settings)
    assert "synthetic-database-private-value" not in repr(settings)


def test_github_wrong_owner_rejected(db, settings):
    from unittest.mock import AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from observatory.api import create_app

    settings.github_client_id = "synthetic"
    app = create_app(settings, db)
    provider = MagicMock()
    provider.authorize_access_token = AsyncMock(return_value={"access_token": "synthetic"})
    response = MagicMock()
    response.json.return_value = {"id": "some-other-owner"}
    provider.get = AsyncMock(return_value=response)
    app.state.oauth._clients["owner"] = provider
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert c.get("/auth/callback").status_code == 403
        assert c.get("/api/status").status_code == 401


def test_owner_session_requires_csrf_and_origin(db, settings):
    from unittest.mock import AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from observatory.api import create_app

    settings.github_client_id = "synthetic"
    app = create_app(settings, db)
    provider = MagicMock()
    provider.authorize_access_token = AsyncMock(return_value={"access_token": "synthetic"})
    response = MagicMock()
    response.json.return_value = {"id": settings.owner_subject}
    provider.get = AsyncMock(return_value=response)
    app.state.oauth._clients["owner"] = provider
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert c.get("/auth/callback").status_code == 200
        assert c.get("/api/status").status_code == 200
        assert c.post("/api/logout", json={}).status_code == 403
        csrf = c.get("/api/session").json()["csrf"]
        assert (
            c.post(
                "/api/logout", json={}, headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.invalid"}
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/api/logout", json={}, headers={"X-CSRF-Token": csrf, "Origin": settings.public_url}
            ).status_code
            == 200
        )


def test_unexpected_provider_error_not_exposed_or_logged(client, read_headers, monkeypatch, caplog):
    def fail(_):
        raise RuntimeError("synthetic-secret-that-must-not-leak")

    monkeypatch.setattr("observatory.api.status", fail)
    response = client.get("/api/status", headers=read_headers)
    assert response.status_code == 500
    assert "synthetic-secret-that-must-not-leak" not in response.text
    assert "synthetic-secret-that-must-not-leak" not in caplog.text
