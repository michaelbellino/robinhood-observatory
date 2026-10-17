from datetime import timedelta
from unittest.mock import AsyncMock
from sqlalchemy import select, func
import pytest
from observatory.db import Database, State, Snapshot, Event, Work, now, aware
from observatory.broker import Throttled, MalformedData
from observatory.worker import run_cycle, record_events, notifications, heartbeat
from observatory.service import change_pause, status


def payload():
    return {
        "source": "synthetic_fixture",
        "portfolio": {"total_value": "123", "buying_power": {"buying_power": "20"}},
        "equity_positions": [{"symbol": "TEST", "quantity": "2"}],
        "option_positions": [],
        "equity_orders": [],
        "option_orders": [],
        "warnings": [],
        "quotes": [],
        "option_quotes": [],
        "contracts": [],
    }


class FakeBroker:
    def __init__(self, *args):
        self.metrics = {"requests": 7}

    async def initialize(self):
        pass

    async def close(self):
        pass


@pytest.fixture
def fake_collection(monkeypatch):
    fn = AsyncMock(return_value=payload())
    monkeypatch.setattr("observatory.worker.collect", fn)
    monkeypatch.setattr("observatory.worker.heartbeat", AsyncMock(return_value=True))
    return fn


async def test_monitoring_without_model(configured, settings, vault, fake_collection):
    result = await run_cycle(configured, settings, vault, FakeBroker)
    assert result == "succeeded"
    with configured.session() as s:
        state = s.get(State, 1)
        assert state.last_success is not None
        assert state.checkpoint["snapshot_id"]
        assert state.analysis_status == "disabled_or_budget_exhausted"


async def test_restart_keeps_pause_checkpoint(configured, settings, vault, fake_collection):
    await run_cycle(configured, settings, vault, FakeBroker)
    change_pause(configured, "synthetic-owner", True, "00000000-0000-4000-8000-000000000001")
    restarted = Database(settings.database_url)
    assert await run_cycle(restarted, settings, vault, FakeBroker) == "idle"
    with restarted.session() as s:
        assert s.get(State, 1).checkpoint["snapshot_id"]
        assert s.get(State, 1).paused


async def test_stale_failure_preserves_last_state(configured, settings, vault, fake_collection):
    await run_cycle(configured, settings, vault, FakeBroker)
    with configured.session() as s:
        s.get(State, 1).next_run = now() - timedelta(seconds=1)
        prior = s.get(State, 1).last_success
    fake_collection.side_effect = MalformedData()
    assert await run_cycle(configured, settings, vault, FakeBroker) == "malformed_data"
    with configured.session() as s:
        assert s.get(State, 1).last_success == prior
        assert s.scalar(select(func.count()).select_from(Snapshot)) == 1
    assert status(configured)["snapshot"]["portfolio"]["total_value"] == "123"


async def test_throttle_delay_persists(configured, settings, vault, fake_collection):
    fake_collection.side_effect = Throttled(1800)
    assert await run_cycle(configured, settings, vault, FakeBroker) == "throttled"
    with configured.session() as s:
        assert (aware(s.get(State, 1).next_run) - now()).total_seconds() > 1795


async def test_token_expiry_fails_closed(configured, settings, vault, fake_collection):
    with configured.session() as s:
        vault.put(
            s,
            "robinhood_tokens",
            {"access_token": "synthetic", "expires_at": (now() - timedelta(seconds=1)).isoformat()},
        )
    assert await run_cycle(configured, settings, vault, FakeBroker) == "authorization_expired"
    fake_collection.assert_not_called()
    with configured.session() as s:
        assert s.get(State, 1).paused
        assert not s.get(State, 1).account_verified
        assert s.get(State, 1).last_success is None


async def test_database_failure_no_heartbeat(settings, vault, monkeypatch):
    beat = AsyncMock()
    monkeypatch.setattr("observatory.worker.heartbeat", beat)

    class Broken:
        def session(self):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        await run_cycle(Broken(), settings, vault, FakeBroker)
    beat.assert_not_called()


def test_events_deduplicate(db):
    p = payload()
    p["equity_orders"] = [{"id": "synthetic-order", "state": "queued"}]
    with db.session() as s:
        record_events(s, p, payload())
    with db.session() as s:
        record_events(s, p, payload())
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 1


def test_position_order_is_not_a_change(db):
    before, after = payload(), payload()
    before["equity_positions"].append({"symbol": "OTHER", "quantity": "1"})
    after["equity_positions"] = list(reversed(before["equity_positions"]))
    with db.session() as s:
        assert record_events(s, after, before, "synthetic-snapshot-1") == []
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 0


def test_repeated_position_transition_is_a_new_event_but_replay_is_not(db):
    before, after = payload(), payload()
    after["equity_positions"][0]["quantity"] = "3"
    with db.session() as s:
        first = record_events(s, after, before, "synthetic-snapshot-1")
    with db.session() as s:
        assert record_events(s, after, before, "synthetic-snapshot-1") == []
    with db.session() as s:
        record_events(s, before, after, "synthetic-snapshot-2")
    with db.session() as s:
        repeated = record_events(s, after, before, "synthetic-snapshot-3")
    assert len(first) == len(repeated) == 1
    assert first != repeated
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 3


async def test_notification_is_generic_and_not_duplicated(db, settings, respx_mock):
    settings.notification_url = "https://notify.test/receiver"
    settings.notification_token = "synthetic"
    with db.session() as s:
        s.add(Event(id="synthetic-event", kind="order", payload={"balance": "private-test-value"}))
    route = respx_mock.post(settings.notification_url).respond(200)
    await notifications(db, settings)
    await notifications(db, settings)
    assert route.call_count == 1
    assert b"private-test-value" not in route.calls[0].request.content


async def test_ambiguous_notification_not_replayed(db, settings, respx_mock):
    import httpx

    settings.notification_url = "https://notify.test/receiver"
    with db.session() as s:
        s.add(Event(id="synthetic-event", kind="order", payload={}))
    route = respx_mock.post(settings.notification_url).mock(side_effect=httpx.ReadTimeout("synthetic"))
    await notifications(db, settings)
    await notifications(db, settings)
    assert route.call_count == 1
    with db.session() as s:
        assert s.get(Event, "synthetic-event").delivery_state == "delivery_unknown"


async def test_successful_work_only_heartbeat(configured, settings, vault, fake_collection, monkeypatch):
    beat = AsyncMock()
    monkeypatch.setattr("observatory.worker.heartbeat", beat)
    fake_collection.side_effect = MalformedData()
    await run_cycle(configured, settings, vault, FakeBroker)
    beat.assert_not_called()


async def test_heartbeat_failure_recorded(db, settings, respx_mock):
    settings.heartbeat_url = "https://heartbeat.test/private-path"
    respx_mock.post(settings.heartbeat_url).respond(500)
    assert not await heartbeat(db, settings)
    with db.session() as s:
        assert s.get(State, 1).last_heartbeat is None


async def test_interrupted_job_explicit(configured, settings, vault, fake_collection):
    with configured.session() as s:
        s.add(Work(id="interrupted-job"))
    await run_cycle(configured, settings, vault, FakeBroker)
    with configured.session() as s:
        assert s.get(Work, "interrupted-job").state == "interrupted"


async def test_pause_during_read_discards_result(configured, settings, vault, monkeypatch):
    async def collect_and_pause(*args):
        change_pause(configured, "synthetic-owner", True, "00000000-0000-4000-8000-000000000002")
        return payload()

    monkeypatch.setattr("observatory.worker.collect", collect_and_pause)
    assert await run_cycle(configured, settings, vault, FakeBroker) == "superseded"
    with configured.session() as s:
        assert s.scalar(select(func.count()).select_from(Snapshot)) == 0
