"""Synthetic broker fixtures: exercise the real workflow without real orders."""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from observatory import trade_review as review
from observatory.db import TradeIdea, Snapshot, Event, now
from observatory.broker import PreviewBroker, ReadOnlyBroker
from tests.test_rehearsal import owner_session


def idea_body(**changes):
    return {"request_id": str(uuid4()), "terms": {"asset": "equity", "symbol": "TEST", "side": "buy", "quantity": 1, "limit_price": "1"},
            "rationale": "Synthetic workflow fixture, never a recommendation.", "risks": "Synthetic fixture only; no real brokerage action.",
            "methodology": "test-only/v1", "sources": ["synthetic fixture"], **changes}


def owner_body(**changes):
    return {"request_id": str(uuid4()), "terms": idea_body()["terms"], **changes}


def quote():
    return {"symbol": "TEST", "bid_price": "0.99", "ask_price": "1.01", "venue_bid_time": now().isoformat(), "venue_ask_time": now().isoformat()}


class FakePreview:
    def __init__(self, *args):
        self.calls = []
    async def initialize(self):
        pass
    async def close(self):
        pass
    async def call(self, name, args):
        assert name == "review_equity_order"
        return {"symbol": args["symbol"], "side": args["side"], "quantity": "1.0000", "type": "limit",
                "limit_price": args["limit_price"], "order_checks": {"alert_type": "FIXTURE_WARNING"},
                "market_data_disclosure": "Fixture NBBO disclosure; display unchanged.", "quote_data": quote()}


async def preview_fixture(db, settings, vault):
    idea = review.create_owner_ticket(db, settings.owner_subject, review.OwnerTicket.model_validate(owner_body()))
    return await review.preview(db, settings, vault, idea["id"], settings.owner_subject, FakePreview)


def decision(idea, **changes):
    return review.Decision(request_id=uuid4(), terms_hash=idea["terms_hash"], preview_hash=idea["preview_hash"],
                           decision="prepare_in_robinhood", acknowledge_manual_submission=True, **changes)


async def test_real_review_flow_is_not_a_trade(configured, settings, vault):
    idea = await preview_fixture(configured, settings, vault)
    assert idea["preview"]["market_data_disclosure"] == "Fixture NBBO disclosure; display unchanged."
    request = decision(idea)
    result = review.decide(configured, settings.owner_subject, idea["id"], request)
    assert result["status"] == "ready_for_manual_submission"
    assert result["sent_to_broker"] is None and result["broker_order"] is None
    assert review.decide(configured, settings.owner_subject, idea["id"], request)["status"] == result["status"]
    with configured.session() as s:
        assert s.scalar(select(func.count()).select_from(Event)) == 1


def test_idempotency_dedup_expiry_and_caps(configured, settings):
    body = review.OwnerTicket.model_validate(owner_body())
    first = review.create_owner_ticket(configured, settings.owner_subject, body)
    assert first["payload"]["origin"] == "owner_directed"
    assert review.create_owner_ticket(configured, settings.owner_subject, body)["id"] == first["id"]
    body.request_id = uuid4()
    assert review.create_owner_ticket(configured, settings.owner_subject, body)["id"] == first["id"]
    body.request_id = UUID(first["id"])
    body.terms.limit_price = Decimal("2")
    with pytest.raises(HTTPException, match="Request ID"):
        review.create_owner_ticket(configured, settings.owner_subject, body)
    with configured.session() as s:
        s.get(TradeIdea, first["id"]).expires_at = now()-timedelta(seconds=1)
    assert review.list_ideas(configured)[0]["status"] == "expired"


@pytest.mark.parametrize("failure", ["preview", "terms", "origin", "expiry", "changed_preview"])
async def test_review_rejects_staleness_and_changes(configured, settings, vault, failure):
    idea = await preview_fixture(configured, settings, vault)
    request = decision(idea)
    with configured.session() as s:
        row = s.get(TradeIdea, idea["id"])
        if failure == "preview":
            row.preview_at = now()-timedelta(minutes=2)
        if failure == "terms":
            row.payload = {**row.payload, "rationale": "Changed explanation"}
        if failure == "origin":
            row.payload = {**row.payload, "origin": "research_recommendation"}
        if failure == "expiry":
            row.expires_at = now()-timedelta(seconds=1)
        if failure == "changed_preview":
            row.preview = {**row.preview, "order_checks": {"changed": True}}
    with pytest.raises(HTTPException) as error:
        review.decide(configured, settings.owner_subject, idea["id"], request)
    assert error.value.status_code == 409


def test_no_broker_write_method_in_either_adapter():
    for cls in (ReadOnlyBroker, PreviewBroker):
        broker = cls("fixture", "fixture")
        for name in ("place_equity_order", "place_option_order", "cancel_equity_order", "replace_option_order"):
            broker.schemas[name] = {"type": "object"}
            with pytest.raises(PermissionError):
                broker.validate_call(name, {"account_number": "fixture"})


def test_auth_and_csrf(client, configured, settings, control_headers, read_headers):
    assert client.get("/api/trades").status_code == 401
    assert client.post("/api/trades", headers=read_headers, json=idea_body()).status_code == 403
    assert client.post("/api/trades", headers=control_headers,
                       json=idea_body(methodology="owner-directed")).status_code == 409
    # Control bearer credentials cannot turn the owner ticket path into a bypass.
    assert client.post("/api/trades/owner-ticket", headers=control_headers, json=owner_body()).status_code == 403
    idea = review.create_owner_ticket(configured, settings.owner_subject, review.OwnerTicket.model_validate(owner_body()))
    assert client.post(f'/api/trades/{idea["id"]}/preview', headers=control_headers).status_code == 403
    owner_session(client, settings)
    assert client.post(f'/api/trades/{idea["id"]}/preview').status_code == 403


def test_unproven_research_mandate_denied_even_if_methodology_spoofed(configured, settings):
    for methodology in ("test-only/v1", "owner-directed"):
        candidate = review.NewIdea.model_validate(idea_body(methodology=methodology))
        with pytest.raises(HTTPException) as failure:
            review.create(configured, settings.owner_subject, candidate)
        assert failure.value.status_code == 409
    with configured.session() as s:
        assert s.scalar(select(func.count()).select_from(TradeIdea)) == 0


async def test_linking_requires_matching_observed_order(configured, settings, vault):
    idea = await preview_fixture(configured, settings, vault)
    review.decide(configured, settings.owner_subject, idea["id"], decision(idea))
    order_id = str(uuid4())
    with pytest.raises(HTTPException):
        review.link_order(configured, settings.owner_subject, idea["id"], order_id)
    order = {"id": order_id, "symbol": "TEST", "side": "buy", "quantity": "1", "price": "1", "type": "limit",
             "time_in_force": "gfd", "market_hours": "regular_hours", "created_at": now().isoformat(),
             "state": "partially_filled", "cumulative_quantity": "0.5"}
    with configured.session() as s:
        s.add(Snapshot(id=str(uuid4()), source="robinhood_mcp_live", payload={"equity_orders": [order]}))
    # The snapshot does not prove immutable account provenance. Even a matching
    # observed order cannot be associated with this proposal until it does.
    with pytest.raises(HTTPException) as failure:
        review.link_order(configured, settings.owner_subject, idea["id"], order_id)
    assert failure.value.status_code == 409


def test_option_shape_and_preview_echo():
    leg = {"option_id": str(uuid4()), "expiration": "2030-01-18", "strike": "100", "right": "call",
           "side": "buy", "position_effect": "open", "multiplier": 100}
    terms = review.Terms(asset="option", symbol="TEST", quantity=1, limit_price="1", legs=[leg])
    args = terms.broker_arguments("fixture")
    review.validate_echo(terms, {**args, "order_checks": {}, "direction": "debit", "option_quotes": [{"instrument_id": leg["option_id"]}]})
    with pytest.raises(Exception):
        review.validate_echo(terms, {**args, "order_checks": {}, "direction": "credit"})
    with pytest.raises(ValueError):
        review.Terms(asset="option", symbol="TEST", quantity=1, limit_price="1", legs=[leg, leg], direction="debit")

