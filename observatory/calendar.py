from datetime import timedelta, timezone
from functools import lru_cache
import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=8)
def calendar(name):
    # Reviewed mappings only. Unknown contract hours must not be inferred from its underlying.
    if name not in {"XNYS", "XNAS", "24/7"}:
        raise ValueError("Unverified calendar mapping")
    return xcals.get_calendar(name)


def session_state(name, at):
    if name is None:
        return "unknown"
    if name == "24/7":
        return "open"
    return "open" if calendar(name).is_open_on_minute(pd.Timestamp(at).floor("min")) else "closed"


def poll_delay(settings, config, snapshot, at):
    mappings = config.get("calendars", {})
    symbols = set(config.get("watchlist", []))
    symbols.update(p["symbol"] for p in snapshot.get("equity_positions", []))
    # Options stay on conservative active cadence until contract-specific hours are verified.
    if snapshot.get("option_positions") or snapshot.get("crypto_positions"):
        return settings.active_poll_seconds
    states = [session_state(mappings.get(symbol), at) for symbol in symbols]
    if any(x in {"open", "unknown"} for x in states):
        return settings.active_poll_seconds
    # Cash-only account: XNYS is a reconciliation cadence, not an asserted instrument schedule.
    return (
        settings.active_poll_seconds
        if session_state("XNYS", at) == "open"
        else settings.offhours_poll_seconds
    )


def next_full_session(at):
    cal = calendar("XNYS")
    for n in range(14):
        day = pd.Timestamp((at + timedelta(days=n)).date())
        if cal.is_session(day):
            start, end = cal.session_open(day).to_pydatetime(), cal.session_close(day).to_pydatetime()
            if start > at:
                return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    raise ValueError("No upcoming session in calendar")


def quote_session_context(name, at, source_times=()):
    """Reference-calendar context only, never broker hours or quote validity.

    A configured equity calendar describes regular sessions, not extended trading,
    contract-specific options hours, halts or the broker's ability to accept orders.
    Unknown mappings and calendar range failures deliberately remain unknown.
    """
    unknown = {"state": "unknown", "source": "unverified", "calendar": None,
               "broker_session_verified": False, "quote_context": "unknown",
               "next_regular_open": None, "previous_regular_close": None}
    if name not in {"XNYS", "XNAS"}:
        return unknown
    try:
        cal = calendar(name)
        minute = pd.Timestamp(at).floor("min")
        active = cal.is_open_on_minute(minute)
        context = {**unknown, "state": "regular_open" if active else "outside_regular_session",
                   "source": "configured_reference_calendar", "calendar": name}
        if active:
            context["quote_context"] = "current_regular_session_requires_fresh_quote"
            return context
        prior = cal.minute_to_session(minute, direction="previous")
        close = cal.session_close(prior).to_pydatetime()
        opening = cal.session_open(prior).to_pydatetime()
        next_open = cal.next_open(minute).to_pydatetime()
        context.update(previous_regular_close=close, next_regular_open=next_open)
        stamps = tuple(source_times)
        if len(stamps) == 2 and all(stamp is not None for stamp in stamps):
            if all(opening <= stamp <= close for stamp in stamps):
                context["quote_context"] = "prior_regular_session_not_current_quote"
            else:
                context["quote_context"] = "outside_prior_regular_session_or_older"
        return context
    except (ValueError, TypeError, KeyError, OverflowError):
        # Expired calendar coverage is a data gap, not evidence that a market closed.
        return unknown
