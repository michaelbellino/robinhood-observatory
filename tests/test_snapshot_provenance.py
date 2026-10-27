"""Synthetic account provenance and read-only manual order observations; no real broker I/O."""
from datetime import timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from observatory import trade_review as review
from observatory.db import State, Snapshot, TradeIdea, now
from observatory.rehearsal import fingerprint
from tests.test_collection import SchemaBroker
from observatory.collection import collect


def seeded(configured):
    with configured.session() as s:
        state = s.get(State, 1)
        state.account_binding = str(uuid4())
        payload = {"terms": {"asset": "equity", "symbol": "TEST", "side": "buy", "quantity": "1",
                             "limit_price": "1", "type": "limit", "time_in_force": "gfd",
                             "market_hours": "regular_hours"}, "origin": "owner_directed"}
        row = TradeIdea(id=str(uuid4()), payload=payload, terms_hash=fingerprint(payload), request_hash="f"*64,
                        expires_at=now()+timedelta(minutes=5), status="ready_for_manual_submission",
                        account_cipher=state.account_cipher, account_binding=state.account_binding,
                        preview_generation=state.generation, reviewed_at=now()-timedelta(seconds=1))
        s.add(row)
        return row.id


def order():
    return {"id": str(uuid4()), "symbol": "TEST", "side": "buy", "quantity": "1", "price": "1",
            "type": "limit", "trigger": "immediate", "time_in_force": "gfd", "market_hours": "regular_hours",
            "created_at": now().isoformat(), "placed_agent": "user", "state": "partially_filled",
            "cumulative_quantity": "0.5"}


def snapshot(configured, ident, orders, **changes):
    with configured.session() as s:
        state = s.get(State, 1)
        row = Snapshot(id=str(uuid4()), source="robinhood_mcp_live", account_binding=state.account_binding,
                       generation=state.generation, observed_at=now(), collection_started_at=now(),
                       payload={"equity_orders": orders, "feed_metadata": {"equity_orders": {"complete": True}}})
        for key, value in changes.items():
            setattr(row, key, value)
        s.add(row)


def test_snapshot_links_only_matching_account_generation(configured):
    ident, record = seeded(configured), order()
    snapshot(configured, ident, [record])
    result = review.link_order(configured, "synthetic-owner", ident, record["id"])
    assert result["status"] == "observed_manual_order"
    assert result["broker_order"]["status"] == "partially_filled"
    assert result["sent_to_broker"] is False
    assert result["broker_order"]["details"]["cumulative_quantity"] == "0.5"
    # Idempotent link is not a second order and does not place anything.
    assert review.link_order(configured, "synthetic-owner", ident, record["id"])["broker_order_id"] == record["id"]


@pytest.mark.parametrize("case", ["wrong_binding", "old_generation", "stale", "incomplete", "legacy",
                                  "duplicate", "not_user", "changed_terms", "before_review"])
def test_snapshot_link_fails_closed(configured, case):
    ident, record = seeded(configured), order()
    changes = {}
    if case == "wrong_binding":
        changes["account_binding"] = str(uuid4())
    if case == "old_generation":
        changes["generation"] = 99
    if case == "stale":
        changes["observed_at"] = now()-timedelta(minutes=3)
    if case == "legacy":
        changes["account_binding"] = None
    if case == "not_user":
        record["placed_agent"] = "agentic"
    if case == "changed_terms":
        record["price"] = "2"
    if case == "before_review":
        record["created_at"] = (now()-timedelta(minutes=2)).isoformat()
    rows = [record, record] if case == "duplicate" else [record]
    if case == "incomplete":
        changes["payload"] = {"equity_orders": rows, "feed_metadata": {"equity_orders": {"complete": False}}}
    snapshot(configured, ident, rows, **changes)
    with pytest.raises(HTTPException) as failure:
        review.link_order(configured, "synthetic-owner", ident, record["id"])
    assert failure.value.status_code == 409


def test_observation_stale_or_changed_connection_is_unknown(configured):
    ident, record = seeded(configured), order()
    snapshot(configured, ident, [record])
    review.link_order(configured, "synthetic-owner", ident, record["id"])
    with configured.session() as s:
        row, state = s.get(TradeIdea, ident), s.get(State, 1)
        row.broker_observed_at = now()-timedelta(minutes=3)
        assert review.serialize(row, state=state)["broker_order"]["status"] == "unknown"
        row.broker_observed_at = now()
        state.generation += 1
        data = review.serialize(row, state=state)
        assert data["broker_order"]["status"] == "unknown"
        assert data["broker_order"]["fresh"] is False


def test_duplicate_cross_proposal_link_rejected(configured):
    first, record = seeded(configured), order()
    snapshot(configured, first, [record])
    review.link_order(configured, "synthetic-owner", first, record["id"])
    with configured.session() as s:
        old = s.get(TradeIdea, first)
        new = TradeIdea(id=str(uuid4()), payload=old.payload, terms_hash=old.terms_hash, request_hash="d"*64,
                        expires_at=old.expires_at, status="ready_for_manual_submission", account_cipher=old.account_cipher,
                        account_binding=old.account_binding, preview_generation=old.preview_generation, reviewed_at=old.reviewed_at)
        s.add(new)
        second = new.id
    with pytest.raises(HTTPException, match="already linked"):
        review.link_order(configured, "synthetic-owner", second, record["id"])


@pytest.mark.parametrize("state,expected", [("new", "submitted"), ("confirmed", "accepted"),
    ("filled", "filled"), ("rejected", "rejected"), ("cancelled", "canceled"), ("unknown-broker-value", "unknown")])
def test_exact_status_mapping(state, expected):
    assert review.normalized_status({"state": state}) == expected


class ExactRead:
    record = None
    seen = []
    def __init__(self, token, account, *args):
        self.account = account
    async def initialize(self):
        pass
    async def close(self):
        pass
    async def call(self, name, args):
        assert name == "get_accounts"
        return {"accounts": [{"account_number": self.account, "state": "active", "agentic_allowed": True}]}
    async def pages(self, name, key, args):
        assert name == "get_equity_orders" and key == "orders"
        assert args == {"account_number": "SYNTHETIC-ACCOUNT", "order_id": self.record["id"]}
        self.seen.append(args)
        return [self.record]


async def test_scoped_read_only_link_and_refresh(configured, settings, vault):
    ident, record = seeded(configured), order()
    ExactRead.record, ExactRead.seen = record, []
    result = await review.observe_manual_order(configured, settings, vault, "synthetic-owner", ident, record["id"], ExactRead)
    assert result["broker_order"]["status"] == "partially_filled"
    ExactRead.record = {**record, "state": "filled", "cumulative_quantity": "1"}
    result = await review.observe_manual_order(configured, settings, vault, "synthetic-owner", ident, record["id"], ExactRead)
    assert result["broker_order"]["status"] == "filled" and len(ExactRead.seen) == 2
    ExactRead.record = record
    with pytest.raises(HTTPException, match="conflicts"):
        await review.observe_manual_order(configured, settings, vault, "synthetic-owner", ident, record["id"], ExactRead)


async def test_collection_records_receipt_without_inventing_source_timestamp():
    data = await collect(SchemaBroker(), {"watchlist": []})
    assert data["collection_started_at"]
    assert data["feed_metadata"]["equity_orders"]["complete"] is True
    assert data["feed_metadata"]["equity_orders"]["received_at"]
    assert data["feed_metadata"]["portfolio"]["source_as_of"] is None
    assert data["feed_metadata"]["advanced_orders"]["complete"] is False
    assert data["open_orders_complete"] is False


async def test_reconnect_allows_exact_linked_order_refresh_only(configured, settings, vault):
    ident, record = seeded(configured), order()
    ExactRead.record, ExactRead.seen = record, []
    await review.observe_manual_order(configured, settings, vault, "synthetic-owner", ident, record["id"], ExactRead)
    with configured.session() as s:
        state = s.get(State, 1)
        state.generation += 1
        assert review.serialize(s.get(TradeIdea, ident), state=state)["broker_order"]["status"] == "unknown"
    result = await review.observe_manual_order(configured, settings, vault, "synthetic-owner", ident, record["id"], ExactRead)
    assert result["broker_order"]["status"] == "partially_filled"
    ExactRead.record = {**record, "id": str(uuid4())}
    with pytest.raises(HTTPException, match="different broker order"):
        await review.observe_manual_order(configured, settings, vault, "synthetic-owner", ident, ExactRead.record["id"], ExactRead)


def test_worker_refresh_does_not_infer_cancel_or_fill_from_absence(configured):
    ident, record = seeded(configured), order()
    snapshot(configured, ident, [record])
    review.link_order(configured, "synthetic-owner", ident, record["id"])
    with configured.session() as s:
        state = s.get(State, 1)
        snap = Snapshot(id=str(uuid4()), source="robinhood_mcp_live", account_binding=state.account_binding,
                        generation=state.generation, observed_at=now(), collection_started_at=now(),
                        payload={"equity_orders": [], "feed_metadata": {"equity_orders": {"complete": True}}})
        review.reconcile_manual_snapshot(s, state, snap)
        assert review.serialize(s.get(TradeIdea, ident), state=state)["broker_order"]["status"] == "unknown"
        snap.payload = {"equity_orders": [{**record, "state": "filled", "cumulative_quantity": "1"}],
                        "feed_metadata": {"equity_orders": {"complete": True}}}
        review.reconcile_manual_snapshot(s, state, snap)
        assert review.serialize(s.get(TradeIdea, ident), state=state)["broker_order"]["status"] == "filled"


@pytest.mark.parametrize("case", ["negative_fill", "too_much_fill", "false_filled", "false_partial"])
def test_ambiguous_quantities_never_become_a_fill(configured, case):
    ident, record = seeded(configured), order()
    if case == "negative_fill":
        record["cumulative_quantity"] = "-1"
    if case == "too_much_fill":
        record["cumulative_quantity"] = "2"
    if case == "false_filled":
        record["state"] = "filled"
    if case == "false_partial":
        record["cumulative_quantity"] = "0"
    snapshot(configured, ident, [record])
    with pytest.raises(HTTPException):
        review.link_order(configured, "synthetic-owner", ident, record["id"])


def test_credential_reenrollment_preserves_immutable_account_mapping(db, settings, vault):
    from observatory.enrollment import LocalEnrollment
    from observatory.security import digest
    with db.session() as s:
        state = s.get(State, 1)
        state.account_cipher = vault.encrypt("SYNTHETIC-ACCOUNT")
        state.account_label = "Synthetic account"
        state.account_binding = "opaque-synthetic-binding"
        state.paused = True
    helper = LocalEnrollment(db, vault, settings)
    ticket = helper.start(settings.owner_subject, 51234, digest("synthetic-proof"))["ticket"]
    helper.complete("Bearer "+ticket, {"proof": "synthetic-proof", "redirect_uri": "http://127.0.0.1:51234/callback",
                    "client_id": "synthetic-client", "token": {"access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh", "expires_in": 3600, "token_type": "Bearer"}})
    with db.session() as s:
        state = s.get(State, 1)
        assert vault.decrypt(state.account_cipher) == "SYNTHETIC-ACCOUNT"
        assert state.account_binding == "opaque-synthetic-binding"
        assert state.account_verified is False


def test_account_selection_same_identity_preserves_binding_rebind_is_rejected(client, db, settings, vault, control_headers):
    with db.session() as s:
        state = s.get(State, 1)
        state.account_cipher = vault.encrypt("SYNTHETIC-ACCOUNT")
        state.account_binding = "opaque-synthetic-binding"
    def selection(account):
        return vault.encrypt({"account": account, "owner": settings.owner_subject, "expires": now().timestamp()+60})
    assert client.post("/api/robinhood/account", headers=control_headers,
                       json={"selection": selection("SYNTHETIC-ACCOUNT")}).status_code == 200
    with db.session() as s:
        state = s.get(State, 1)
        assert state.account_binding == "opaque-synthetic-binding"
    assert client.post("/api/robinhood/account", headers=control_headers,
                       json={"selection": selection("OTHER-SYNTHETIC-ACCOUNT")}).status_code == 409
