"""Adversarial acceptance cases; all account and price inputs are synthetic."""
from datetime import timedelta
import json
import uuid
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from observatory.db import Audit, MarketSample, Observation, State, Work, now
from observatory.domain import Quote, Paper, Leg, paper_cost_range
from observatory.oauth import RobinhoodOAuth, TOKEN
from observatory.broker import AuthorizationExpired
from observatory.market import refresh_quotes
from observatory.service import start_observations, finalize_observations
from test_options_calendar import contract, AT


def test_public_crypto_probe_does_not_turn_match_time_into_quote_time():
    from scripts.paper_feed_probe import evaluate
    result = evaluate({"best_bid":"100", "best_ask":"101", "time":AT.isoformat()}, AT)
    assert result["event_age_seconds"] == 0
    assert result["paper"]["book_freshness"] == "unknown"
    assert result["paper"]["fill_status"] == "unfilled" and result["paper"]["estimated_total_cost"] is None


def paper():
    return Paper(structure="single", legs=[Leg(contract=contract(10), side="buy", quantity=1,
                 position_effect="open")], fees_estimate="1", slippage_per_contract=".1",
                 uncertainty="Synthetic acceptance only", sources=["fixture"])


@pytest.mark.parametrize("offset", [None, -61, 6])
def test_recent_trade_never_refreshes_stale_or_unknown_book(offset):
    stamp = AT + timedelta(seconds=offset) if offset is not None else None
    q = Quote(instrument_id="synthetic-option", bid="1", ask="2", quoted_at=AT,
              bid_at=stamp, ask_at=stamp, received_at=AT)
    assert paper_cost_range(paper(), {q.instrument_id: q}, AT)["estimated_net_debit"] is None


def test_fresh_book_with_old_trade_is_usable_estimate_but_not_fill():
    q = Quote(instrument_id="synthetic-option", bid="1", ask="2", quoted_at=AT-timedelta(hours=2),
              bid_at=AT, ask_at=AT, received_at=AT)
    result = paper_cost_range(paper(), {q.instrument_id: q}, AT)
    assert result["estimated_net_debit"] == "21.1" and result["fill_status"] == "unfilled"


@pytest.mark.parametrize("bid,ask", [(0, 2), (-1, 2), (3, 2)])
def test_invalid_book_cannot_price_paper(bid, ask):
    q = Quote(instrument_id="synthetic-option", bid=bid, ask=ask, bid_at=AT, ask_at=AT, received_at=AT)
    assert paper_cost_range(paper(), {q.instrument_id: q}, AT)["estimated_net_debit"] is None


@pytest.mark.parametrize("strikes,sides", [([20, 25, 24, 30], ["buy","sell","sell","buy"]),
                                        ([20, 25, 30, 35], ["buy","sell","buy","sell"])])
def test_mislabeled_condor_rejected(strikes, sides):
    legs = [Leg(contract=contract(100).model_copy(update={"id":str(i), "strike":strike,
                "right":"put" if i < 2 else "call"}), side=side, quantity=1, position_effect="open")
            for i, (strike, side) in enumerate(zip(strikes, sides))]
    with pytest.raises(ValidationError):
        Paper(structure="iron_condor", legs=legs, uncertainty="Synthetic", sources=["fixture"])


@pytest.mark.parametrize("lifetime", [True, float("inf"), float("nan"), -1])
def test_invalid_token_lifetime_fails_closed(db, vault, settings, lifetime):
    with pytest.raises(ValueError):
        RobinhoodOAuth(db, vault, settings).validate_token(
            {"access_token":"synthetic", "token_type":"Bearer", "expires_in":lifetime})


async def test_refresh_audit_has_metadata_without_credentials(configured, settings, vault, respx_mock):
    with configured.session() as s:
        vault.put(s, "robinhood_tokens", {"access_token":"synthetic-old", "refresh_token":"synthetic-old-refresh",
                  "client_id":"synthetic-client", "expires_at":(now()-timedelta(seconds=1)).isoformat()})
    respx_mock.post(TOKEN).respond(200, json={"access_token":"synthetic-new", "refresh_token":"synthetic-new-refresh",
                                          "token_type":"Bearer", "expires_in":3600})
    oauth = RobinhoodOAuth(configured, vault, settings)
    assert await oauth.access_token() == "synthetic-new"
    diagnostic = oauth.diagnostics(settings.owner_subject)
    assert diagnostic["token_health"]["recent_refreshes"][0]["refresh_credential_changed"]
    assert "synthetic-new" not in json.dumps(diagnostic)
    with configured.session() as s:
        assert len(s.scalars(select(Audit).where(Audit.action=="broker_token_refreshed")).all()) == 1


async def test_malformed_refresh_response_requires_reauthorization(configured, settings, vault, respx_mock):
    with configured.session() as s:
        vault.put(s, "robinhood_tokens", {"access_token":"synthetic", "refresh_token":"synthetic",
                  "client_id":"synthetic-client", "expires_at":(now()-timedelta(seconds=1)).isoformat()})
    route = respx_mock.post(TOKEN).respond(200, content=b"not-json")
    with pytest.raises(AuthorizationExpired):
        await RobinhoodOAuth(configured, vault, settings).access_token()
    assert route.call_count == 1


def test_five_sessions_idempotent_and_audited(db):
    first = start_observations(db, "owner", 5)
    assert first == start_observations(db, "owner", 5) and len(set(first)) == 5
    with db.session() as s:
        assert len(s.scalars(select(Audit).where(Audit.action=="start_observation")).all()) == 5
        rows = s.scalars(select(Observation).order_by(Observation.starts_at)).all()
        assert all(a.ends_at < b.starts_at for a,b in zip(rows, rows[1:]))


def test_observation_schedule_requires_control(client, read_headers, control_headers):
    assert client.post("/api/observation?sessions=5", headers=read_headers).status_code == 403
    assert client.post("/api/observation?sessions=6", headers=control_headers).status_code == 422
    assert len(client.post("/api/observation?sessions=5", headers=control_headers).json()["ids"]) == 5


@pytest.mark.parametrize("book_state,expected", [("fresh","passed"), ("stale","failed_or_incomplete"),
                                               ("missing","failed_or_incomplete"), (None,"failed_or_incomplete")])
def test_full_session_needs_quote_and_account_continuity(db, book_state, expected):
    end, start = now()-timedelta(seconds=1), now()-timedelta(seconds=301)
    with db.session() as s:
        s.add(Observation(id="synthetic", starts_at=start, ends_at=end, status="pending", evidence={}))
        # A cycle begun before the window counts only when it finished inside it.
        s.add(Work(id="synthetic", started_at=start-timedelta(seconds=40), finished_at=start+timedelta(seconds=20),
                   state="succeeded", metrics={}))
        if book_state:
            for offset in [30, 110, 190, 270]:
                s.add(MarketSample(id=str(uuid.uuid4()), observed_at=start+timedelta(seconds=offset),
                                   payload={"instruments":[{"asset":"equity", "instrument_id":"TEST",
                                            "state":book_state, "source_age_seconds":1}]}))
    finalize_observations(db)
    with db.session() as s:
        row = s.get(Observation,"synthetic")
        assert row.status == expected and row.evidence["account_continuity_passed"]


async def test_quality_samples_atomic_and_bounded(configured, settings):
    with configured.session() as s:
        s.get(State,1).config = {"watchlist":["TEST"],"calendars":{}}
        s.add(MarketSample(id="expired", observed_at=now()-timedelta(days=15), payload={}))
    class Broker:
        metrics = {"requests":0}
        call = AsyncMock(return_value={"results":[]})
    await refresh_quotes(configured, settings, Broker())
    with configured.session() as s:
        samples = s.scalars(select(MarketSample)).all()
        assert len(samples) == 1 and samples[0].payload["instruments"][0]["state"] == "missing"
        assert "bid" not in samples[0].payload and "ask" not in samples[0].payload
