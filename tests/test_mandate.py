"""Owner confirmation and fail-closed live recommendation gating; synthetic only."""
import base64
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from itsdangerous import TimestampSigner
from sqlalchemy import select

from observatory import mandate, trade_review
from observatory.db import Event, Snapshot, State, TradeIdea, now


def owner_headers(client, settings):
    encoded = base64.b64encode(json.dumps({"sub": settings.owner_subject, "csrf": "mandate-csrf"}).encode())
    client.cookies.set("observatory_session", TimestampSigner(settings.session_secret).sign(encoded).decode())
    return {"X-CSRF-Token": "mandate-csrf", "Origin": settings.public_url}


def choose_account(db, vault):
    with db.session() as session:
        state = session.get(State, 1)
        state.account_cipher = vault.encrypt("SYNTHETIC0001")
        state.account_binding = str(uuid4())
        state.account_label = "Synthetic account ••••0001"
        state.account_verified = True
        state.generation = 1


def body():
    return {"request_id": str(uuid4()), "expected_version": 0, "confirm_account_last4": "0001",
            "universe": "us_listed_equities_etfs", "symbols": [], "excluded_symbols": [], "exclusions": [],
            "strategies": ["defined_risk_credit_spread", "long_call"], "proposed_budget_usd": "50.00",
            "max_loss_per_trade_usd": "20.00", "max_aggregate_loss_usd": "50.00", "max_collateral_usd": "50.00",
            "min_holding_days": 0, "max_holding_days": 7, "notifications": "material_only",
            "expires_at": (now() + timedelta(days=7)).isoformat(), "acknowledge_recommendations_only": True}


def test_owner_mandate_requires_explicit_account_and_is_versioned(client, db, vault, settings, control_headers):
    choose_account(db, vault)
    assert client.get("/api/research-mandate", headers=control_headers).status_code == 403
    headers = owner_headers(client, settings)
    assert client.get("/api/research-mandate", headers=headers).json()["mode"] == "demo"
    original = body()
    wrong_account = {**original, "confirm_account_last4": "9999"}
    assert client.post("/api/research-mandate", headers=headers, json=wrong_account).status_code == 409
    assert client.post("/api/research-mandate", headers=headers, json={**original, "acknowledge_recommendations_only": False}).status_code == 422
    first = client.post("/api/research-mandate", headers=headers, json=original)
    assert first.status_code == 200, first.text
    assert first.json()["mode"] == "confirmed_pending_validation"
    assert first.json()["recommendations_active"] is False
    assert first.json()["limits"]["proposed_budget_usd"] == "50.00"
    assert client.post("/api/research-mandate", headers=headers, json=original).json()["version"] == 1
    changed = {**original, "request_id": str(uuid4()), "max_loss_per_trade_usd": "10.00"}
    assert client.post("/api/research-mandate", headers=headers, json=changed).status_code == 409
    changed["expected_version"] = 1
    second = client.post("/api/research-mandate", headers=headers, json=changed)
    assert second.status_code == 200 and second.json()["version"] == 2
    assert client.post("/api/research-mandate", headers=headers, json=original).json()["mode"] == "superseded"
    disabled = client.post("/api/research-mandate/disable", headers=headers,
                           json={"request_id": str(uuid4()), "expected_version": 2})
    assert disabled.status_code == 200 and disabled.json()["mode"] == "inactive"


def test_account_changes_and_incomplete_feeds_block_live_gate(db, vault):
    choose_account(db, vault)
    mandate.save(db, vault, "synthetic-owner", mandate.MandateInput.model_validate(body()))
    with db.session() as session:
        state = session.get(State, 1)
        snapshot = Snapshot(id=str(uuid4()), source="robinhood_mcp_live", observed_at=now()-timedelta(minutes=10),
                            account_binding=state.account_binding, generation=state.generation,
                            payload={"open_orders_complete": False, "portfolio_as_of": None, "feed_metadata": {}})
        session.add(snapshot)
    with db.session() as session:
        state = session.get(State, 1)
        result = mandate.live_proposal_gate(session, state)
        assert result["allowed"] is False
        assert {"snapshot_stale", "portfolio_source_timestamp_stale_or_unknown",
                "portfolio_feed_coverage_or_freshness_incomplete", "open_order_coverage_incomplete",
                "candidate_risk_evaluation_required"} <= set(result["reasons"])
        state.account_binding = str(uuid4())
    with db.session() as session:
        result = mandate.live_proposal_gate(session, session.get(State, 1))
        assert "mandate_account_changed" in result["reasons"]
        assert "account_bound_snapshot_missing" in result["reasons"]


def test_complete_recent_feeds_do_not_bypass_unimplemented_risk_engine(db, vault):
    choose_account(db, vault)
    mandate.save(db, vault, "synthetic-owner", mandate.MandateInput.model_validate(body()))
    timestamp = now().isoformat()
    names = ("portfolio", "equity_positions", "option_positions", "equity_orders", "option_orders",
             "advanced_orders", "crypto_positions", "crypto_orders")
    with db.session() as session:
        state = session.get(State, 1)
        session.add(Snapshot(id=str(uuid4()), source="robinhood_mcp_live", observed_at=now(),
                             account_binding=state.account_binding, generation=state.generation,
                             payload={"open_orders_complete": True, "portfolio_as_of": timestamp,
                                      "feed_metadata": {name: {"received_at": timestamp, "complete": True}
                                                        for name in names}}))
    with db.session() as session:
        gate = mandate.live_proposal_gate(session, session.get(State, 1))
        assert gate["allowed"] is False
        assert gate["reasons"] == ["candidate_risk_evaluation_required"]


def test_external_proposal_cannot_claim_owner_directed_origin(db, vault):
    choose_account(db, vault)
    proposal = trade_review.NewIdea(request_id=uuid4(), terms=trade_review.Terms(asset="equity", symbol="TEST",
        side="buy", quantity="1", type="limit", limit_price="1"), rationale="Synthetic test reason",
        risks="Synthetic test risk", methodology="owner-directed", sources=["Synthetic source"])
    with pytest.raises(HTTPException) as error:
        trade_review.create(db, "synthetic-owner", proposal)
    assert error.value.status_code == 409
    with db.session() as session:
        assert session.scalar(select(TradeIdea)) is None
        assert session.scalar(select(Event)) is None
    ticket = trade_review.create_owner_ticket(db, "synthetic-owner", trade_review.OwnerTicket(
        request_id=uuid4(), terms=proposal.terms))
    assert ticket["payload"]["origin"] == "owner_directed"
