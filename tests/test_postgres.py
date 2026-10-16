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
