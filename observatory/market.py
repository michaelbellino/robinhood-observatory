"""One serialized quote batch at a time, sharing the broker's durable request budget."""
import time
import uuid
from datetime import timedelta
from sqlalchemy import select, delete
from .broker import AuthorizationExpired, BrokerError, MalformedData, Throttled
from .calendar import poll_delay, quote_session_context
from .db import MarketState, MarketSample, Snapshot, State, aware, now
from .domain import Quote, quote_from_broker


def targets(config, snapshot):
    equities = sorted(set(config.get("watchlist", [])) | {
        x["symbol"] for x in snapshot.get("equity_positions", [])
    })
    options = sorted({x["option_id"] for x in snapshot.get("option_positions", [])})
    if len(equities) > 200 or len(options) > 200:
        raise MalformedData()
    return [(kind, items[i:i + 20]) for kind, items in [("equity", equities), ("option", options)]
            for i in range(0, len(items), 20)]


def market_status(db):
    with db.session() as s:
        state, market = s.get(State, 1), s.get(MarketState, 1)
        if market is None:
            return {"status": "not_collected", "source": "robinhood_mcp_polling", "quotes": []}
        payload = dict(market.payload)
        quotes = []
        observed = now()
        mappings = (state.config or {}).get("calendars", {})
        for item in payload.get("quotes", []):
            quote = Quote.model_validate({k: v for k, v in item.items() if k != "asset"})
            side_ages = [(observed - at).total_seconds() for at in (quote.bid_at, quote.ask_at) if at]
            source_age = max(side_ages) if len(side_ages) == 2 else None
            quote_state = quote.book_freshness(observed)
            # Never infer option hours from its underlying or refresh stale prices
            # merely because a reference exchange calendar is outside session.
            mapping = mappings.get(quote.instrument_id) if item.get("asset") == "equity" else None
            session_context = quote_session_context(mapping, observed, (quote.bid_at, quote.ask_at))
            quotes.append({**item, "trade_freshness": quote.freshness(observed),
                           "quote_freshness": quote_state, "source_age_seconds": source_age,
                           "session_context": session_context,
                           "fresh_book_data": quote_state == "fresh",
                           "execution_readiness": "not_assessed",
                           "source_freshness_max_age_seconds": 60,
                           "receipt_age_seconds": (observed - quote.received_at).total_seconds()})
        next_run = aware(market.next_run)
        status = "paused" if state.paused else market.status
        if not state.paused and next_run and now() > next_run + timedelta(seconds=60):
            status = "stalled"
        return {**payload, "status": status, "source": "robinhood_mcp_polling", "quotes": quotes,
                "last_success": aware(market.last_success), "next_run": next_run,
                "metrics": market.metrics, "streaming": False}


async def refresh_quotes(db, settings, broker):
    with db.session() as s:
        state = s.get(State, 1)
        market = s.get(MarketState, 1)
        if state.paused or not state.account_verified:
            return "paused"
        if market is None:
            market = MarketState(id=1)
            s.add(market)
            s.flush()
        if market.next_run and aware(market.next_run) > now():
            return "idle"
        snap = s.scalar(select(Snapshot).where(Snapshot.source == "robinhood_mcp_live")
                        .order_by(Snapshot.observed_at.desc()))
        snapshot = snap.payload if snap else {}
        config, generation = state.config, state.generation
        batches = targets(config, snapshot)
        if not batches:
            market.status = "no_instruments"
            market.payload = {"quotes": [], "requested_instruments": [], "missing_instruments": []}
            market.next_run = now() + timedelta(seconds=settings.quote_poll_seconds)
            return "no_instruments"
        kind, batch = batches[market.cursor % len(batches)]
        prior = dict(market.payload)
        # Unknown hours retain active sampling; closed verified sessions reduce quote work.
        delay = settings.quote_poll_seconds
        if poll_delay(settings, config, snapshot, now()) == settings.offhours_poll_seconds:
            delay = max(delay, 300)
        market.last_attempt = now()
        market.next_run = now() + timedelta(seconds=delay)
    started, before = time.monotonic(), dict(broker.metrics)
    try:
        name = "get_equity_quotes" if kind == "equity" else "get_option_quotes"
        key = "symbols" if kind == "equity" else "instrument_ids"
        data = await broker.call(name, {key: batch})
        rows = data.get("results")
        if not isinstance(rows, list):
            raise MalformedData()
        quotes = [quote_from_broker(x, now(), options=kind == "option") for x in rows]
        returned = {q.instrument_id for q in quotes}
        if len(returned) != len(quotes) or not returned.issubset(set(batch)):
            raise MalformedData()
        wanted = {(asset, ident) for asset, ids in batches for ident in ids}
        cache = {(q["asset"], q["instrument_id"]): q for q in prior.get("quotes", [])
                 if (q["asset"], q["instrument_id"]) in wanted}
        for q in quotes:
            cache[(kind, q.instrument_id)] = {**q.model_dump(mode="json"), "asset": kind}
        missing = {(x["asset"], x["instrument_id"]) for x in prior.get("missing_instruments", [])}
        missing = (missing & wanted) - {(kind, ident) for ident in batch}
        missing |= {(kind, ident) for ident in set(batch) - returned}
        missing |= wanted - set(cache)
        with db.session() as s:
            state = s.scalar(select(State).where(State.id == 1).with_for_update())
            if state.paused or state.generation != generation:
                return "superseded"
            market = s.get(MarketState, 1)
            market.last_success = now()
            market.next_run = now() + timedelta(seconds=delay)
            market.cursor = (market.cursor + 1) % len(batches)
            market.status = "incomplete" if missing else "polling"
            market.payload = {"quotes": list(cache.values()),
                "requested_instruments": [{"asset": a, "instrument_id": i} for a, i in sorted(wanted)],
                "missing_instruments": [{"asset": a, "instrument_id": i} for a, i in sorted(missing)],
                "batch_target_seconds": delay, "batches": len(batches),
                "coverage_note": "Target per batch; pacing, reconnects and slow reads may extend gaps"}
            market.metrics = {k: broker.metrics[k] - before.get(k, 0) for k in broker.metrics}
            market.metrics["observed_rate_headers"] = getattr(broker, "rate_headers", {})
            market.metrics["configured_request_cap_per_minute"] = settings.broker_rpm
            market.metrics["batch_seconds"] = round(time.monotonic() - started, 3)
            observed = now()
            by_id = {q.instrument_id: q for q in quotes}
            samples = []
            for ident in batch:
                quote = by_id.get(ident)
                stamps = [quote.bid_at, quote.ask_at] if quote else []
                age = max((observed - x).total_seconds() for x in stamps) if stamps and all(stamps) else None
                samples.append({"asset": kind, "instrument_id": ident,
                                "state": quote.book_freshness(observed) if quote else "missing",
                                "source_age_seconds": age})
            s.add(MarketSample(id=str(uuid.uuid4()), observed_at=observed,
                              payload={"instruments": samples, "metrics": dict(market.metrics)}))
            s.execute(delete(MarketSample).where(MarketSample.observed_at < observed - timedelta(days=14)))
        return "succeeded"
    except AuthorizationExpired:
        raise
    except (BrokerError, ValueError) as exc:
        with db.session() as s:
            market = s.get(MarketState, 1)
            market.status = exc.code if isinstance(exc, BrokerError) else "malformed_data"
            wait = max(delay, exc.retry_after) if isinstance(exc, Throttled) else max(delay, 60)
            market.next_run = now() + timedelta(seconds=wait)
        return "failed"  # Preserve previous values and their original timestamps.
