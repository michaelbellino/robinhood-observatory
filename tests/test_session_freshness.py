"""Session context never upgrades exchange timestamps or asserts broker tradability."""
from datetime import datetime

import pytest

from observatory.calendar import quote_session_context
from observatory.db import MarketState, State
from observatory.market import market_status


def at(value):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize("mapping", [None, "UNKNOWN", "24/7"])
def test_unverified_session_mapping_stays_unknown(mapping):
    context = quote_session_context(mapping, at("2026-10-29T12:30:00+00:00"))
    assert context["state"] == "unknown"
    assert context["broker_session_verified"] is False


def test_premarket_prior_session_is_context_not_freshness():
    stamp = at("2026-10-28T19:59:00+00:00")
    context = quote_session_context("XNYS", at("2026-10-29T12:30:00+00:00"), (stamp, stamp))
    assert context["state"] == "outside_regular_session"
    assert context["quote_context"] == "prior_regular_session_not_current_quote"
    assert context["source"] == "configured_reference_calendar"
    assert context["broker_session_verified"] is False
    assert context["next_regular_open"] == at("2026-10-29T13:30:00+00:00")


def test_regular_open_requires_current_quote():
    context = quote_session_context("XNYS", at("2026-10-29T15:00:00+00:00"))
    assert context["state"] == "regular_open"
    assert context["quote_context"] == "current_regular_session_requires_fresh_quote"


def test_weekend_and_dst_next_open_from_calendar_not_wallclock_guess():
    context = quote_session_context("XNYS", at("2026-10-31T15:00:00+00:00"))
    assert context["next_regular_open"] == at("2026-11-02T14:30:00+00:00")
    assert context["previous_regular_close"] == at("2026-10-30T20:00:00+00:00")


def test_prior_session_missing_and_older_times_not_conflated():
    observation = at("2026-10-29T12:30:00+00:00")
    stamp = at("2026-10-27T19:59:00+00:00")
    assert quote_session_context("XNYS", observation, (stamp, None))["quote_context"] == "unknown"
    assert quote_session_context("XNYS", observation, (stamp, stamp))["quote_context"] == "outside_prior_regular_session_or_older"


def test_calendar_failure_is_unknown(monkeypatch):
    def unavailable(name):
        raise ValueError("calendar range exhausted")
    monkeypatch.setattr("observatory.calendar.calendar", unavailable)
    assert quote_session_context("XNYS", at("2026-10-29T12:30:00+00:00"))["state"] == "unknown"


def test_market_api_keeps_stale_books_and_option_sessions_unknown(configured, monkeypatch):
    observed = at("2026-10-29T12:30:00+00:00")
    monkeypatch.setattr("observatory.market.now", lambda: observed)
    quote = {"instrument_id": "TEST", "bid": "1", "ask": "2",
             "bid_at": "2026-10-28T19:59:00+00:00", "ask_at": "2026-10-28T19:59:00+00:00",
             "received_at": observed.isoformat()}
    with configured.session() as session:
        session.get(State, 1).config = {"calendars": {"TEST": "XNYS"}}
        session.add(MarketState(id=1, payload={"quotes": [
            {**quote, "asset": "equity"}, {**quote, "asset": "option"}]}))
    equity, option = market_status(configured)["quotes"]
    for row in (equity, option):
        assert row["quote_freshness"] == "stale"
        assert row["fresh_book_data"] is False
        assert row["execution_readiness"] == "not_assessed"
        assert row["receipt_age_seconds"] == 0
    assert equity["session_context"]["quote_context"] == "prior_regular_session_not_current_quote"
    assert option["session_context"]["state"] == "unknown"


def test_reference_holiday_and_short_session_are_not_guessed_as_normal_day():
    holiday = quote_session_context("XNYS", at("2026-11-26T16:00:00+00:00"))
    assert holiday["state"] == "outside_regular_session"
    assert holiday["next_regular_open"] == at("2026-11-27T14:30:00+00:00")
    short_day = quote_session_context("XNYS", at("2026-11-27T19:00:00+00:00"))
    assert short_day["state"] == "outside_regular_session"
    assert short_day["previous_regular_close"] == at("2026-11-27T18:00:00+00:00")
