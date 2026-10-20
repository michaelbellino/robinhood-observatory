"""Real submission code, synthetic HTTP transport only. Never calls a live broker."""
import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from observatory import manual_orders as manual, trade_review as review
from observatory.db import TradeIdea, OrderSubmission, State, now
from tests.test_trade_review import idea_body, quote
from tests.test_rehearsal import owner_session


def ready(db, settings, asset="equity", custom_terms=None):
    terms = {"asset": "equity", "symbol": "TEST", "side": "buy", "quantity": 1, "limit_price": "1"}
    if asset == "option":
        terms = {"asset": "option", "symbol": "TEST", "quantity": 1, "limit_price": "0.10",
                 "legs": [{"option_id": str(uuid4()), "expiration": "2030-01-18", "strike": "100", "right": "call",
                           "side": "buy", "position_effect": "open", "multiplier": 100}]}
    terms = custom_terms or terms
    idea = review.create(db, settings.owner_subject, review.NewIdea.model_validate(idea_body(terms=terms)))
    t = review.Terms.model_validate(terms)
    preview = t.broker_arguments("SYNTHETIC-ACCOUNT") | {"order_checks": {}, "requested_terms": t.model_dump(mode="json")}
    if asset == "equity":
        preview["quote_data"] = quote()
    else:
        preview.update(direction=t.direction or ("debit" if t.legs[0].side == "buy" else "credit"), fees={"total_fee": "0.01"},
                       option_quotes=[{"instrument_id": str(leg.option_id), "bid_price": "0.09",
                                       "ask_price": "0.10", "updated_at": now().isoformat()} for leg in t.legs])
    with db.session() as s:
        row = s.get(TradeIdea, idea["id"])
        row.preview, row.preview_at, row.preview_generation = preview, now(), s.get(State, 1).generation
    return idea, manual.SubmitOrder(terms_hash=idea["terms_hash"], preview_hash=review.fingerprint(preview),
                                   acknowledge_real_order=True)


class NoWait:
    def __init__(self, *args):
        pass
    async def acquire(self):
        pass
    def defer(self, *args):
        pass


@pytest.fixture
def transport(monkeypatch):
    manual._sessions.clear()
    monkeypatch.setattr(manual, "RequestLimiter", NoWait)
    class Fixture:
        sent = []
        failure = None
        before_send = None
        response_gate = None
        initialized = False
        initializations = 0
        account_changes = {}
        tradability_changes = {}
        order_changes = {}
        positions = []
        extended_chain = True
        chain_underlyings = []
        def factory(self, token, account, rpm):
            async def sleep(_):
                pass
            async def handler(request):
                assert request.url.host == "agent.robinhood.com"
                body = json.loads(request.content)
                method, params = body["method"], body["params"]
                result = {}
                if method == "initialize":
                    self.initializations += 1
                if method == "tools/list":
                    names = ["get_accounts", "place_equity_order", "place_option_order", "get_equity_tradability",
                             "get_option_chains", "get_equity_positions", "get_option_positions"]
                    # A strict placement schema proves review-only fields are removed.
                    keys = ["account_number", "quantity", "type", "market_hours", "time_in_force",
                            "ref_id", "symbol", "side", "limit_price", "price", "legs", "direction", "stop_price",
                            "dollar_amount", "symbols", "underlying_symbol", "nonzero", "option_ids", "cursor"]
                    result = {"tools": [{"name": name, "inputSchema": {"type": "object",
                              "properties": {key: {} for key in keys}, "additionalProperties": False}} for name in names]}
                elif method == "tools/call":
                    name, args = params["name"], params["arguments"]
                    if name == "get_accounts":
                        if self.before_send:
                            self.before_send()
                        data = {"accounts": [{"account_number": account, "agentic_allowed": True,
                                "state": "active", "deactivated": False, "permanently_deactivated": False,
                                "option_level": "option_level_3", "type": "limited_margin",
                                "brokerage_account_type": "individual", **self.account_changes}]}
                    elif name == "get_equity_tradability":
                        data = {"results": [{"symbol": args["symbols"][0], "state": "active", "tradeable": True,
                                "fractional_tradability": "tradable", "all_day_tradability": "tradable",
                                "account_type_tradabilities": [{"account_type": "individual", "account_type_tradability": "tradable"}],
                                **self.tradability_changes}]}
                    elif name == "get_option_chains":
                        data = {"chains": [{"symbol": args["underlying_symbol"], "underlying_instruments": self.chain_underlyings,
                                "extended_hours_state": "enabled" if self.extended_chain else "disabled"}]}
                    elif name in {"get_equity_positions", "get_option_positions"}:
                        data = {"positions": self.positions}
                    else:
                        assert name in {"place_equity_order", "place_option_order"}
                        self.sent.append((name, args))
                        if self.response_gate:
                            await self.response_gate.wait()
                        if self.failure == "timeout":
                            raise httpx.ReadTimeout("synthetic timeout")
                        if self.failure == "broker_error":
                            return httpx.Response(200, json={"id": body["id"], "result": {"isError": True}})
                        order = {**args, "id": str(uuid4()), "state": "queued", "created_at": now().isoformat(),
                                 "trigger": "stop" if args["type"].startswith("stop_") else "immediate",
                                 "type": args["type"].removeprefix("stop_"), "cumulative_quantity": "0", "processed_quantity": "0"}
                        if name == "place_equity_order":
                            order["price"] = order.pop("limit_price", None)
                            if "dollar_amount" in args:
                                order["dollar_based_amount"] = {"amount": args["dollar_amount"], "currency_code": "USD"}
                        else:
                            order.update(direction=args.get("direction", "debit" if args["legs"][0]["side"] == "buy" else "credit"),
                                         chain_symbol="TEST", trade_value_multiplier="100")
                        if self.failure == "wrong_terms":
                            order["quantity"] = "999"
                        data = {"order": order | self.order_changes}
                    result = {"structuredContent": {"data": data}}
                return httpx.Response(200, headers={"Mcp-Session-Id": "synthetic-session"},
                                      json={"id": body.get("id"), "result": result})
            broker = manual.OwnerOrderBroker(token, account, rpm,
                    client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=sleep)
            broker.owns_client = True
            return broker
    return Fixture()


@pytest.mark.parametrize("asset", ["equity", "option"])
async def test_exact_order_and_repeated_click_send_once(configured, settings, vault, transport, asset):
    idea, body = ready(configured, settings, asset)
    first = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    second = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert first["state"] == second["state"] == "acknowledged"
    assert first["order"]["state"] == "queued"
    assert len(transport.sent) == 1 and transport.sent[0][1]["ref_id"] == first["ref_id"]
    assert "chain_symbol" not in transport.sent[0][1] and "underlying_type" not in transport.sent[0][1]
    assert review.list_ideas(configured)[0]["sent_to_broker"]


@pytest.mark.parametrize("failure", ["timeout", "broker_error", "wrong_terms"])
async def test_unknown_outcome_never_resends(configured, settings, vault, transport, failure):
    idea, body = ready(configured, settings)
    transport.failure = failure
    first = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    second = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert first["state"] == second["state"] == "unknown"
    assert not first["retry_allowed"] and len(transport.sent) == 1
    # A newly named proposal must not bypass the unresolved order lock.
    with configured.session() as s:
        s.get(TradeIdea, idea["id"]).status = "submission_unknown"
    other, other_body = ready(configured, settings)
    with pytest.raises(HTTPException, match="Another submission"):
        manual.reserve(configured, settings.owner_subject, other["id"], other_body)


@pytest.mark.parametrize("change", ["stale", "generation", "paused", "alerts", "terms", "expiry"])
async def test_changes_during_broker_wait_prevent_transmission(configured, settings, vault, transport, change):
    idea, body = ready(configured, settings)
    def mutate():
        with configured.session() as s:
            row, state = s.get(TradeIdea, idea["id"]), s.get(State, 1)
            if change == "stale":
                row.preview_at = now()-timedelta(minutes=2)
            if change == "generation":
                state.generation += 1
            if change == "paused":
                state.paused = True
            if change == "alerts":
                row.preview = {**row.preview, "order_checks": {"alert_type": "NEW_WARNING"}}
            if change == "terms":
                row.payload = {**row.payload, "rationale": "Changed during submission"}
            if change == "expiry":
                row.expires_at = now()-timedelta(seconds=1)
    transport.before_send = mutate
    outcome = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert outcome["state"] == "not_sent" and not transport.sent


async def test_competing_requests_and_preview_decision_are_blocked(configured, settings, vault, transport):
    idea, body = ready(configured, settings)
    transport.response_gate = asyncio.Event()
    task = asyncio.create_task(manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory))
    for _ in range(100):
        await asyncio.sleep(0)
        if transport.sent:
            break
    assert transport.sent
    other = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert other["state"] == "transmitting"
    with pytest.raises(HTTPException, match="Submission already started"):
        await review.preview(configured, settings, vault, idea["id"], settings.owner_subject)
    with pytest.raises(HTTPException, match="Submission already started"):
        review.decide(configured, settings.owner_subject, idea["id"], review.Decision(request_id=uuid4(),
                      terms_hash=body.terms_hash, decision="reject", acknowledge_manual_submission=True))
    transport.response_gate.set()
    assert (await task)["state"] == "acknowledged" and len(transport.sent) == 1


def test_submit_requires_owner_session_and_exact_ack(client, configured, settings, control_headers, read_headers, monkeypatch):
    idea, body = ready(configured, settings)
    path = f'/api/trades/{idea["id"]}/submit'
    calls = []
    async def fake_submit(*args):
        calls.append(args)
        return {"state": "synthetic_test_only"}
    monkeypatch.setattr(manual, "submit", fake_submit)
    assert client.post(path, json=body.model_dump(mode="json")).status_code == 401
    for headers in (control_headers, read_headers):
        assert client.post(path, headers=headers, json=body.model_dump(mode="json")).status_code == 403
    headers = owner_session(client, settings)
    assert client.post(path, json=body.model_dump(mode="json")).status_code == 403
    invalid = body.model_dump(mode="json") | {"acknowledge_real_order": False}
    assert client.post(path, headers=headers, json=invalid).status_code == 422
    assert not calls
    assert client.post(path, headers=headers, json=body.model_dump(mode="json")).status_code == 200
    assert len(calls) == 1


async def test_placement_adapter_requires_exact_grant(transport):
    broker = transport.factory("synthetic", "SYNTHETIC-ACCOUNT", 6)
    try:
        await broker.initialize()
        args = {"account_number": "SYNTHETIC-ACCOUNT", "symbol": "TEST", "side": "buy", "quantity": "1"}
        with pytest.raises(PermissionError):
            await broker.call("place_equity_order", args)
        broker.grant = ("place_equity_order", review.fingerprint(args))
        with pytest.raises(PermissionError):
            await broker.call("place_equity_order", args | {"quantity": "2"})
        with pytest.raises(PermissionError):
            await broker.call("place_equity_order", args | {"account_number": "OTHER"})
        assert not transport.sent
    finally:
        await broker.close()


def test_stale_preparation_is_never_taken_over(configured, settings):
    idea, body = ready(configured, settings)
    manual.reserve(configured, settings.owner_subject, idea["id"], body)
    with configured.session() as s:
        s.get(OrderSubmission, idea["id"]).updated_at = now()-timedelta(minutes=4)
    reservation, existing = manual.reserve(configured, settings.owner_subject, idea["id"], body)
    assert reservation is None and existing["state"] == "unknown" and not existing["retry_allowed"]


async def test_metadata_cache_never_reuses_submission_grant(transport):
    first = transport.factory("synthetic", "SYNTHETIC-ACCOUNT", 6)
    await first.initialize()
    first.grant, first.consumed = ("place_equity_order", "fake-hash"), True
    await first.close()
    second = transport.factory("synthetic", "SYNTHETIC-ACCOUNT", 6)
    await second.initialize()
    assert transport.initializations == 1
    assert second.grant is None and not second.consumed
    await second.close()
    third = transport.factory("different-token", "SYNTHETIC-ACCOUNT", 6)
    await third.initialize()
    assert transport.initializations == 2
    await third.close()


@pytest.mark.parametrize("changes", [{"agentic_allowed": False}, {"state": "restricted"},
    {"option_level": "option_level_0"}, {"user_option_level": "option_level_0"},
    {"brokerage_account_type": "trust_revocable"}])
async def test_account_eligibility_is_checked_live(configured, settings, vault, transport, changes):
    idea, body = ready(configured, settings, "option")
    transport.account_changes = changes
    outcome = await manual.submit(configured, settings, vault, idea["id"], settings.owner_subject, body, transport.factory)
    assert outcome["state"] == "not_sent" and not transport.sent
