"""Research status is visible even when no actionable proposal can be made."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from observatory import push
from observatory.db import Analysis, Event, PushDelivery, State, now
from observatory.research import VERSION, run_research_cycle
from tests.test_push import subscription


@pytest.mark.parametrize("enabled", [False, True])
async def test_no_trade_source_errors_notify_once_per_persisted_cycle(configured, settings, enabled):
    with configured.session() as session:
        state = session.get(State, 1)
        state.account_binding = "synthetic-binding"
        state.config = {"research_generation_enabled": enabled}
    snapshot = SimpleNamespace(id=str(uuid4()), account_binding="synthetic-binding", generation=0,
                               observed_at=now(), payload={})
    assert await run_research_cycle(configured, settings, snapshot) == "research_only"
    assert await run_research_cycle(configured, settings, snapshot) == "not_due"
    with configured.session() as session:
        reports = session.scalars(select(Analysis)).all()
        events = session.scalars(select(Event).where(Event.kind == "research_report")).all()
        assert len(reports) == len(events) == 1
        assert reports[0].payload["source_errors"]
        assert reports[0].payload["outcome"] == "no_trade"
        assert reports[0].payload["live_order_count"] == 0
        assert events[0].id == "research:" + reports[0].id
        assert events[0].payload == {"analysis_id": reports[0].id}
        assert session.scalar(select(func.count()).select_from(Event).where(Event.kind == "trade_idea")) == 0


async def test_superseded_cycle_does_not_publish_status(configured, settings):
    snapshot = SimpleNamespace(id=str(uuid4()), account_binding="wrong-binding", generation=0,
                               observed_at=now(), payload={})
    assert await run_research_cycle(configured, settings, snapshot) == "superseded"
    with configured.session() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 0


async def test_research_push_is_private_nontrade_deduplicated(db, settings, vault):
    push.application_key(db, vault)
    push.subscribe(db, vault, settings.owner_subject, subscription())
    ident = str(uuid4())
    with db.session() as session:
        session.add(Analysis(id=ident, snapshot_id=str(uuid4()), strategy_version=VERSION,
                             model="deterministic-public-source", payload={"secret": "PRIVATE-PORTFOLIO"}))
        session.add(Event(id="research:" + ident, kind="research_report", payload={"analysis_id": ident}))
    sent = []
    def sender(sub, keys, payload, origin, ttl):
        sent.append(payload)
        return SimpleNamespace(status_code=201)
    await push.dispatch(db, settings, vault, sender)
    await push.dispatch(db, settings, vault, sender)
    assert len(sent) == 1
    assert sent[0]["title"] == "Research status updated"
    assert sent[0]["url"] == "/trades#research"
    assert "No order was created" in sent[0]["body"]
    assert "PRIVATE-PORTFOLIO" not in json.dumps(sent)
    with db.session() as session:
        assert session.scalar(select(PushDelivery)).state == "accepted_by_push_service"


async def test_missing_research_report_suppresses_notification(db, settings, vault):
    push.application_key(db, vault)
    push.subscribe(db, vault, settings.owner_subject, subscription())
    with db.session() as session:
        session.add(Event(id="research:" + str(uuid4()), kind="research_report",
                          payload={"analysis_id": str(uuid4())}))
    def fail_send(*args):
        pytest.fail("A missing report cannot produce a research notification")
    await push.dispatch(db, settings, vault, fail_send)
    with db.session() as session:
        assert session.scalar(select(PushDelivery)).state == "expired"
