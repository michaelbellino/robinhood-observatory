"""Observed release evidence never substitutes a configured pin or account status."""
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


def schema_table(db, revisions):
    with db.engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        for revision in revisions:
            connection.execute(text("INSERT INTO alembic_version (version_num) VALUES (:revision)"), {"revision": revision})


def test_public_release_reports_runtime_commit_and_observed_current_revision(client, db, monkeypatch):
    commit = "a" * 40
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", commit)
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1
    schema_table(db, heads)
    response = client.get("/health/release")
    assert response.status_code == 200  # Public diagnostics require no owner session.
    result = response.json()
    assert result["process"] == "alive" and result["commit_sha"] == commit
    assert result["commit_state"] == "observed" and result["commit_source"] == "RAILWAY_GIT_COMMIT_SHA"
    assert result["schema_state"] == "observed" and result["schema_revisions"] == heads
    assert result["broker_or_worker_health"] == "not_assessed"
    assert response.headers["Cache-Control"] == "no-store"
    assert "account" not in response.text and "token" not in response.text


def test_missing_runtime_commit_and_schema_stay_unknown(client, monkeypatch):
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    result = client.get("/health/release").json()
    assert result["commit_sha"] is None and result["commit_state"] == "unknown"
    assert result["schema_state"] == "unknown" and result["schema_revisions"] == []


def test_malformed_commit_and_multiple_heads_are_not_release_success(client, db, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "PRIVATE_VALUE_MUST_NOT_APPEAR")
    schema_table(db, ["synthetic_old_head", "synthetic_new_head"])
    response = client.get("/health/release")
    result = response.json()
    assert result["commit_sha"] is None and "PRIVATE_VALUE" not in response.text
    assert result["schema_state"] == "ambiguous" and len(result["schema_revisions"]) == 2


def test_unavailable_database_does_not_disclose_connection_error(client, db, monkeypatch):
    def unavailable():
        raise OperationalError("PRIVATE_CONNECTION_DETAIL", {}, Exception("PRIVATE_DRIVER_DETAIL"))
    monkeypatch.setattr(db.engine, "connect", unavailable)
    response = client.get("/health/release")
    assert response.status_code == 200
    assert response.json()["schema_state"] == "unknown"
    assert "PRIVATE" not in response.text
