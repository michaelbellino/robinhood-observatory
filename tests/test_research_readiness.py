"""Readiness is evidence, never authorization or a fabricated live status."""
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest

from observatory.db import Analysis, Snapshot, State, now
from observatory.research import VERSION
from observatory.research_readiness import readiness


def configured_report(db, *, age=None, errors=None, generation=1):
    at = now()
    config = {"research_sources": [{"symbol": "TEST", "cik": "123"}],
              "research_sec_user_agent": "SyntheticResearch private-contact@example.test",
              "research_generation_enabled": True}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    with db.session() as session:
        state = session.get(State, 1)
        state.config, state.account_binding = config, "synthetic-binding"
        state.generation, state.account_verified, state.paused = 1, True, False
        snapshot_id = str(uuid4())
        session.add(Snapshot(id=snapshot_id, source="robinhood_mcp_live", account_binding="synthetic-binding",
                             generation=generation, payload={}))
        session.add(Analysis(id=str(uuid4()), snapshot_id=snapshot_id, created_at=at-(age or timedelta()),
                             strategy_version=VERSION, model="synthetic", payload={
                                 "source_config_hash": config_hash, "source_errors": errors or [],
                                 "generation": {"status": "available", "reason": "Do not expose this prose"},
                                 "secret": "do-not-return-report-payload"}))
    return at


def test_unconfigured_is_not_ready_and_read_does_not_mutate(db, settings):
    result = readiness(db, settings)
    assert result["mode"] == "not_ready"
    assert not result["actionable_proposals_ready"]
    assert "research_run_not_observed" in result["blockers"]
    assert not result["model"]["configuration_complete"]
    with db.session() as session:
        assert session.get(State, 1).paused


def test_observed_metadata_is_not_actionable_or_worker_model_proof(db, settings):
    at = configured_report(db)
    settings.openai_key, settings.openai_model = "DO-NOT-EXPOSE-KEY", "synthetic-model"
    settings.ai_daily_calls = 2
    settings.ai_daily_usd = settings.ai_monthly_usd = "1"
    settings.ai_input_per_million = settings.ai_output_per_million = "1"
    result = readiness(db, settings, at=at)
    assert result["mode"] == "research_only_observed"
    assert not result["actionable_proposals_ready"]
    assert result["model"]["configuration_complete"]
    assert not result["model"]["worker_configuration_verified"]
    assert result["sources"]["content_scope"] == "sec_metadata_with_optional_bounded_primary_excerpts"
    assert result["last_research"]["content_verified_count"] == 0
    serialized = json.dumps(result)
    for forbidden in ("DO-NOT-EXPOSE", "private-contact", "do-not-return", "Do not expose", "synthetic-binding"):
        assert forbidden not in serialized


@pytest.mark.parametrize("age,generation", [(timedelta(hours=5), 1), (timedelta(), 0),
                                           (timedelta(seconds=-10), 1)])
def test_stale_superseded_or_future_report_not_ready(db, settings, age, generation):
    at = configured_report(db, age=age, generation=generation)
    assert not readiness(db, settings, at=at)["research_only_observed"]


def test_configuration_change_invalidates_previous_success(db, settings):
    at = configured_report(db)
    with db.session() as session:
        state = session.get(State, 1)
        state.config = {**state.config, "research_generation_enabled": False}
    result = readiness(db, settings, at=at)
    assert not result["last_research"]["current_source_configuration"]
    assert not result["research_only_observed"]


def test_source_error_text_is_not_reflected(db, settings):
    at = configured_report(db, errors=["secret contact URL or untrusted source text"])
    result = readiness(db, settings, at=at)
    assert result["last_research"]["source_error_count"] == 1
    assert not result["research_only_observed"]
    assert "secret contact" not in json.dumps(result)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "bad", None])
def test_invalid_or_nonpositive_pricing_fails_closed(db, settings, value):
    settings.ai_input_per_million = value
    settings.ai_output_per_million = "1"
    assert not readiness(db, settings)["model"]["pricing_configured"]


def test_invalid_source_separated_from_valid_contact(db, settings):
    configured_report(db)
    with db.session() as session:
        state = session.get(State, 1)
        state.config = {**state.config, "research_sources": [{"symbol": "bad", "cik": "123"}]}
    result = readiness(db, settings)
    assert result["sources"]["contact_configured"]
    assert not result["sources"]["valid"]


def test_paused_monitor_does_not_show_ready_from_previous_run(db, settings):
    at = configured_report(db)
    with db.session() as session:
        session.get(State, 1).paused = True
    result = readiness(db, settings, at=at)
    assert not result["research_only_observed"]
    assert not result["monitor_active"]


def test_content_count_requires_observed_verified_nonempty_excerpts(db, settings):
    from sqlalchemy import select
    at = configured_report(db)
    with db.session() as session:
        report = session.scalar(select(Analysis).where(Analysis.strategy_version == VERSION))
        report.payload = {**report.payload, "leads": [
            {"content_status": "verified", "content_excerpt": "PRIVATE-EXCERPT-NOT-RETURNED"},
            {"content_status": "unavailable", "content_excerpt": "untrusted text"},
            {"content_status": "verified", "content_excerpt": " "},
            {"content_status": "verified"},
            "malformed",
        ]}
    result = readiness(db, settings, at=at)
    assert result["last_research"]["content_verified_count"] == 1
    assert "PRIVATE-EXCERPT" not in json.dumps(result)
    assert not result["actionable_proposals_ready"]
