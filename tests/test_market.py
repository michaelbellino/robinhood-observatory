"""Synthetic feed tests, independent of a live or open exchange."""
from datetime import timedelta
from unittest.mock import AsyncMock
import json
import httpx
import pytest
from observatory.broker import ReadOnlyBroker, MalformedData, SessionExpired
from observatory.db import State, MarketState, now
from observatory.market import refresh_quotes, market_status
from observatory.service import change_pause
from observatory.worker_session import WorkerBroker


def equity(symbol="TEST"):
    return {"quote": {"symbol": symbol, "bid_price": "10", "ask_price": "11",
                      "venue_bid_time": now().isoformat(), "venue_ask_time": now().isoformat(),
                      "has_traded": False}}


@pytest.fixture
def quote_broker(configured):
    with configured.session() as s:
        s.get(State, 1).config = {"watchlist": ["TEST"], "calendars": {}}
    class Broker:
        metrics = {"requests": 0, "throttles": 0, "elapsed_ms": 0}
        call = AsyncMock(return_value={"results": [equity()]})
    return Broker()


async def test_quotes_commit_without_full_account_cycle(configured, settings, quote_broker):
    assert await refresh_quotes(configured, settings, quote_broker) == "succeeded"
    result = market_status(configured)
    assert result["quotes"][0]["bid"] == "10"
    assert result["quotes"][0]["quoted_at"] is None  # A receipt is not a trade timestamp.
    assert result["quotes"][0]["trade_freshness"] == "unknown"
    assert result["streaming"] is False
    with configured.session() as s:
        assert s.get(State, 1).last_success is None  # Quotes do not make account reconciliation healthy.
    assert await refresh_quotes(configured, settings, quote_broker) == "idle"
    assert quote_broker.call.await_count == 1


async def test_quote_failure_retains_previous_prices(configured, settings, quote_broker):
    await refresh_quotes(configured, settings, quote_broker)
    with configured.session() as s:
        s.get(MarketState, 1).next_run = now() - timedelta(seconds=1)
    quote_broker.call.side_effect = MalformedData()
    assert await refresh_quotes(configured, settings, quote_broker) == "failed"
    result = market_status(configured)
    assert result["status"] == "malformed_data"
    assert result["quotes"][0]["bid"] == "10"


async def test_missing_quote_is_explicit_not_zero(configured, settings, quote_broker):
    quote_broker.call.return_value = {"results": []}
    await refresh_quotes(configured, settings, quote_broker)
    result = market_status(configured)
    assert result["status"] == "incomplete"
    assert result["quotes"] == []
    assert result["missing_instruments"] == [{"asset": "equity", "instrument_id": "TEST"}]


async def test_pause_discards_inflight_quotes(configured, settings, quote_broker):
    async def pause(*args):
        change_pause(configured, "synthetic-owner", True, "00000000-0000-4000-8000-000000000090")
        return {"results": [equity()]}
    quote_broker.call.side_effect = pause
    assert await refresh_quotes(configured, settings, quote_broker) == "superseded"
    assert market_status(configured)["quotes"] == []


async def test_throttle_persists_shared_backoff_and_last_values(configured, settings, quote_broker):
    from observatory.broker import Throttled
    quote_broker.call.side_effect = Throttled(3600)
    assert await refresh_quotes(configured, settings, quote_broker) == "failed"
    assert (market_status(configured)["next_run"] - now()).total_seconds() > 3595


def test_market_endpoint_requires_auth(client, read_headers):
    assert client.get("/api/market").status_code == 401
    assert client.get("/api/market", headers=read_headers).status_code == 200


async def test_session_reuse_and_404_reinitialization():
    calls = []
    expire = False
    def response(request):
        nonlocal expire
        body = json.loads(request.content)
        calls.append(body["method"])
        if expire and body["method"] == "tools/call":
            expire = False
            return httpx.Response(404)
        result = {"tools": [{"name": "get_equity_quotes", "inputSchema": {"type": "object"}}]} if body["method"] == "tools/list" else {"structuredContent": {"results": []}}
        return httpx.Response(200, headers={"Mcp-Session-Id": "synthetic-session"}, json={"id": body.get("id"), "result": result})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        b = WorkerBroker("synthetic", "synthetic", client=client, sleep=AsyncMock())
        await b.initialize()
        await b.initialize()
        assert calls.count("initialize") == 1
        await b.call("get_equity_quotes", {"symbols": ["TEST"]})
        expire = True
        await b.call("get_equity_quotes", {"symbols": ["TEST"]})
        assert calls.count("initialize") == 2
        assert calls.count("tools/call") == 3


async def test_no_recursive_reinitialize_on_persistent_session_failure():
    def response(request):
        body = json.loads(request.content)
        if body["method"] == "tools/list":
            return httpx.Response(404)
        return httpx.Response(200, headers={"Mcp-Session-Id": "synthetic"}, json={"id": body.get("id"), "result": {}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        b = WorkerBroker("synthetic", "synthetic", client=client, sleep=AsyncMock())
        with pytest.raises(SessionExpired):
            await b.initialize()


async def test_pause_or_lost_leadership_checked_after_pacing():
    sent = []
    def response(request):
        sent.append(True)
        return httpx.Response(200, json={"id": 1, "result": {}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        b = ReadOnlyBroker("synthetic", "synthetic", client=client, sleep=AsyncMock())
        b.preflight = AsyncMock(side_effect=RuntimeError("lost lock"))
        with pytest.raises(RuntimeError):
            await b.rpc("initialize")
        assert sent == []


async def test_quotes_interleave_account_reads_only():
    b = WorkerBroker("synthetic", "synthetic")
    b.before_account_call = AsyncMock()
    b.schemas = {"get_accounts": {}, "get_equity_quotes": {}}
    b.rpc = AsyncMock(return_value={"structuredContent": {"results": []}})
    await b.call("get_accounts", {})
    await b.call("get_equity_quotes", {"symbols": ["TEST"]})
    assert b.before_account_call.await_count == 1
    await b.shutdown()

async def test_rotating_batches_cover_equities_and_options(configured, settings, quote_broker):
    from observatory.db import Snapshot
    with configured.session() as s:
        s.add(Snapshot(id="synthetic", source="robinhood_mcp_live", payload={
            "equity_positions": [{"symbol": f"T{i}", "quantity": "1"} for i in range(21)],
            "option_positions": [{"option_id": "synthetic-option", "quantity": "1"}]}))
    async def feed(name, args):
        if name == "get_equity_quotes":
            return {"results": [equity(x) for x in args["symbols"]]}
        return {"results": [{"quote": {"instrument_id": "synthetic-option", "bid_price": "1", "ask_price": "2", "updated_at": now().isoformat()}}]}
    quote_broker.call.side_effect = feed
    for _ in range(3):
        await refresh_quotes(configured, settings, quote_broker)
        with configured.session() as s:
            s.get(MarketState, 1).next_run = now() - timedelta(seconds=1)
    result = market_status(configured)
    assert result["batches"] == 3 and len(result["quotes"]) == 23
    assert result["missing_instruments"] == []
    assert any(q["asset"] == "option" for q in result["quotes"])


async def test_recent_receipt_cannot_make_old_exchange_quotes_fresh(configured, settings, quote_broker):
    row = equity()
    old = (now() - timedelta(minutes=10)).isoformat()
    row["quote"]["venue_bid_time"] = row["quote"]["venue_ask_time"] = old
    quote_broker.call.return_value = {"results": [row]}
    await refresh_quotes(configured, settings, quote_broker)
    quote = market_status(configured)["quotes"][0]
    assert quote["receipt_age_seconds"] < 5
    assert quote["source_age_seconds"] >= 600 and quote["quote_freshness"] == "stale"


async def test_missing_exchange_quote_time_is_unknown(configured, settings, quote_broker):
    row = equity()
    row["quote"].pop("venue_bid_time")
    quote_broker.call.return_value = {"results": [row]}
    await refresh_quotes(configured, settings, quote_broker)
    quote = market_status(configured)["quotes"][0]
    assert quote["source_age_seconds"] is None and quote["quote_freshness"] == "unknown"
