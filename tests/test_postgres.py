"""Real PostgreSQL acceptance gates; never replace with SQLite results."""

import os
import subprocess
import sys
import pytest
from sqlalchemy import text
from observatory.db import Database, State, Base

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL unavailable; gate remains unverified")
    db = Database(url)
    # Only test databases explicitly designated with test or restore in their names.
    assert "test" in db.engine.url.database or db.engine.url.database.startswith("observatory_restore_")
    Base.metadata.drop_all(db.engine)
    db.initialize_test()
    yield db
    Base.metadata.drop_all(db.engine)


def test_single_leader_and_release(pg):
    other = Database(str(pg.engine.url.render_as_string(hide_password=False)))
    with pg.leader() as first:
        assert first is not None
        with other.leader() as second:
            assert second is None
    with other.leader() as recovered:
        assert recovered is not None


def test_leader_crash_recovery(pg):
    with pg.leader() as conn:
        assert conn is not None
        conn.invalidate()
        with pytest.raises(RuntimeError):
            with pg.bound(conn).session():
                pass
    with pg.leader() as recovered:
        assert recovered is not None


def test_pause_persists_new_connection(pg):
    with pg.session() as s:
        s.get(State, 1).paused = True
        s.get(State, 1).checkpoint = {"event": "synthetic"}
    other = Database(pg.engine.url.render_as_string(hide_password=False))
    with other.session() as s:
        assert s.get(State, 1).paused and s.get(State, 1).checkpoint["event"] == "synthetic"


def test_concurrent_native_deposit_is_idempotent(pg, vault, settings):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import select
    from observatory.db import Audit
    from observatory.enrollment import LocalEnrollment
    from observatory.security import digest

    enrollment = LocalEnrollment(pg, vault, settings)
    ticket = enrollment.start(settings.owner_subject, 45678, digest("synthetic-proof"))["ticket"]
    body = {
        "proof": "synthetic-proof",
        "client_id": "synthetic-client",
        "redirect_uri": "http://127.0.0.1:45678/callback",
        "token": {
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enrollment.complete("Bearer " + ticket, body), range(2)))
    assert all(result["connected"] for result in results)
    with pg.session() as s:
        rows = s.scalars(select(Audit).where(Audit.action == "local_enrollment_completed")).all()
        assert len(rows) == 1 and s.get(State, 1).paused


def test_migrations_on_empty_database(pg):
    Base.metadata.drop_all(pg.engine)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": pg.engine.url.render_as_string(hide_password=False)},
        capture_output=True,
    )
    with pg.session() as s:
        assert s.get(State, 1).paused
    with pg.engine.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))


def test_encrypted_backup_isolated_restore(pg, tmp_path, monkeypatch):
    import uuid
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine
    from scripts.backup import backup
    from scripts.restore_verify import restore

    target_name = "observatory_restore_" + uuid.uuid4().hex
    admin = create_engine(pg.engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{target_name}"'))
    target_url = pg.engine.url.set(database=target_name).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", pg.engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv("RESTORE_DATABASE_URL", target_url)
    monkeypatch.setenv("RESTORE_ISOLATED_ACK", "yes")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    try:
        with pg.session() as s:
            s.get(State, 1).checkpoint = {"fixture": "restore-integrity"}
        path = tmp_path / "synthetic.backup.enc"
        backup(path)
        result = restore(path)
        assert result["all_table_hashes_match"] and result["paused"]
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE "{target_name}" WITH (FORCE)'))


def test_market_checkpoint_survives_connection_restart(pg):
    from observatory.db import MarketState, now
    with pg.session() as s:
        s.add(MarketState(id=1, last_success=now(), cursor=2, status="polling", payload={"quotes": [], "synthetic": True}))
    other = Database(pg.engine.url.render_as_string(hide_password=False))
    with other.session() as s:
        row = s.get(MarketState, 1)
        assert row.cursor == 2 and row.payload["synthetic"] and row.last_success


def test_paper_budget_serializes_concurrent_requests(pg):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4
    from fastapi import HTTPException
    from sqlalchemy import select, func
    from observatory.db import Rehearsal
    from observatory import rehearsal

    def create(_):
        try:
            rehearsal.create(pg, "synthetic-owner", rehearsal.CreateRehearsal(
                request_id=uuid4(), terms=rehearsal.TestTerms(
                    asset="equity", symbol="TEST", quantity="1", limit_price="5")))
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(create, range(6)))
    assert results.count(200) == 4 and results.count(429) == 2
    with pg.session() as s:
        assert s.scalar(select(func.sum(Rehearsal.reserved_usd))) == 20


def test_paper_idempotency_survives_concurrent_retry(pg):
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4
    from sqlalchemy import select, func
    from observatory.db import Rehearsal
    from observatory import rehearsal

    request = rehearsal.CreateRehearsal(request_id=uuid4(), terms=rehearsal.fixture("option"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: rehearsal.create(pg, "synthetic-owner", request), range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert sorted(result["replayed"] for result in results) == [False, True]
    with pg.session() as s:
        assert s.scalar(select(func.count()).select_from(Rehearsal)) == 1


def test_concurrent_push_dispatch_claims_once(pg, settings, vault):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from sqlalchemy import select, func
    from observatory import push
    from observatory.db import PushDelivery
    from tests.test_push import ready
    ready(pg, settings, vault)
    calls = []
    lock = threading.Lock()
    def send(*args):
        with lock:
            calls.append(True)
        return SimpleNamespace(status_code=201)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: asyncio.run(push.dispatch(pg, settings, vault, send)), range(2)))
    assert results == [None, None] and len(calls) == 1
    with pg.session() as s:
        assert s.scalar(select(func.count()).select_from(PushDelivery)) == 1
