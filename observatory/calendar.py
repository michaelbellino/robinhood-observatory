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
