from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from observatory.db import Database, RequestBudget, now, aware
from observatory.rate_limit import RequestLimiter
from scripts.restore_verify import restore


def test_rate_budget_shared_across_instances_and_restart(db, settings):
    first = RequestLimiter(db, 6)
    second = RequestLimiter(Database(settings.database_url), 6)
    assert first.reserve() < 0.1
    assert 9.8 < second.reserve() <= 10
    assert 19.8 < first.reserve() <= 20


def test_retry_after_shared_and_never_shortened(db):
    limiter = RequestLimiter(db, 6)
    limiter.defer(3600)
    limiter.defer(1)
    assert 3599 < limiter.reserve() <= 3600
    with db.session() as s:
        assert aware(s.get(RequestBudget, 1).next_allowed) > now() + timedelta(seconds=3600)


async def test_rate_limiter_waits_outside_transaction(db):
    sleep = AsyncMock()
    limiter = RequestLimiter(db, 6, sleep)
    limiter.reserve()
    await limiter.acquire()
    assert 9.8 < sleep.call_args.args[0] <= 10


@pytest.mark.parametrize(
    "target,ack",
    [("postgresql://localhost/production", "yes"), ("postgresql://localhost/observatory_restore_safe", "no")],
)
def test_restore_refuses_production_and_unacknowledged_target(monkeypatch, target, ack):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/production")
    monkeypatch.setenv("RESTORE_DATABASE_URL", target)
    monkeypatch.setenv("RESTORE_ISOLATED_ACK", ack)
    with pytest.raises(ValueError):
        restore("/no/file/read/expected")


async def test_overloaded_requests_do_not_extend_shared_backlog(db):
    from observatory.broker import Throttled
    limiter = RequestLimiter(db, 6, AsyncMock())
    limiter.defer(3600)
    with db.session() as session:
        before = session.get(RequestBudget, 1).next_allowed
    for _ in range(10):
        with pytest.raises(Throttled) as error:
            await limiter.acquire()
        assert error.value.retry_after > 3590
    with db.session() as session:
        assert session.get(RequestBudget, 1).next_allowed == before
    assert not limiter.sleep.called


def test_account_verification_pacing_returns_retry_after(client, control_headers, monkeypatch):
    from observatory.broker import Throttled
    from observatory.oauth import RobinhoodOAuth
    async def paced(*args):
        raise Throttled(31.2)
    monkeypatch.setattr(RobinhoodOAuth, "access_token", paced)
    response = client.get("/api/robinhood/accounts", headers=control_headers)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "32"
    assert "saved connection is unchanged" in response.json()["detail"]
