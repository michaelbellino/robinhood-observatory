"""Reachable research controls use owner consent and never authorize execution."""
from uuid import uuid4

from observatory.db import Audit, State
from test_mandate import owner_headers


def test_candidate_screen_is_authenticated_and_no_trade_is_valid(client, control_headers, read_headers):
    assert client.post("/api/research/compare", json={"candidates": []}).status_code == 401
    assert client.post("/api/research/compare", headers=read_headers, json={"candidates": []}).status_code == 403
    response = client.post("/api/research/compare", headers=control_headers, json={"candidates": []})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["results"] == [] and result["no_trade_valid"]
    assert result["live_order_count"] == 0


def test_sources_are_owner_configured_without_leaking_contact(client, db, settings, control_headers):
    assert client.get("/api/research/sources", headers=control_headers).status_code == 403
    headers = owner_headers(client, settings)
    with db.session() as session:
        session.get(State, 1).config = {"watchlist": ["TEST"], "existing_setting": True}
    request_id = str(uuid4())
    body = {"request_id": request_id, "sources": [{"symbol": "TEST", "cik": "1234567"}],
            "sec_user_agent": "SyntheticResearch fixture-contact@example.test"}
    saved = client.post("/api/research/sources", headers=headers, json=body)
    assert saved.status_code == 200, saved.text
    assert "fixture-contact" not in saved.text
    repeated = client.post("/api/research/sources", headers=headers, json=body)
    assert repeated.json()["repeated"]
    conflict = client.post("/api/research/sources", headers=headers, json={**body, "sources": []})
    assert conflict.status_code == 409
    status = client.get("/api/research/sources", headers=headers)
    assert status.json()["contact_configured"] and "fixture-contact" not in status.text
    with db.session() as session:
        config = session.get(State, 1).config
        assert config["watchlist"] == ["TEST"] and config["existing_setting"]
        assert config["research_sources"] == body["sources"]
        assert "fixture-contact" not in str(session.get(Audit, request_id).details)


def test_source_configuration_rejects_untrusted_hosts_and_duplicate_universe(client, settings):
    headers = owner_headers(client, settings)
    body = {"request_id": str(uuid4()), "sources": [{"symbol": "TEST", "cik": "https://evil.test/"}],
            "sec_user_agent": "SyntheticResearch fixture-contact@example.test"}
    assert client.post("/api/research/sources", headers=headers, json=body).status_code == 422
    body["sources"] = [{"symbol": "TEST", "cik": "123"}, {"symbol": "TEST", "cik": "456"}]
    assert client.post("/api/research/sources", headers=headers, json=body).status_code == 400
