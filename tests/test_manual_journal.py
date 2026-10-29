"""The journal reports broker evidence, never infers executions from local review."""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from observatory import trade_review as review
from observatory.db import State, TradeIdea, now
from tests.test_snapshot_provenance import seeded, order, snapshot


def test_review_without_broker_evidence_has_no_fill_or_profit(configured):
    ident = seeded(configured)
    item = review.list_ideas(configured)[0]
    assert item["id"] == ident
    assert item["observatory_submitted_order"] is False
    assert item["placement_evidence"] == "not_observed"
    assert item["journal"]["broker_status"] == "not_observed"
    assert item["journal"]["filled_quantity"] is None
    assert item["journal"]["realized_pnl"] is None


def test_fresh_partial_fill_and_stale_journal_are_distinct(configured):
    ident, record = seeded(configured), order()
    snapshot(configured, ident, [record])
    result = review.link_order(configured, "synthetic-owner", ident, record["id"])
    assert result["placement_evidence"] == "broker_read_verified"
    assert result["journal"]["broker_status"] == "partially_filled"
    assert result["journal"]["filled_quantity"] == "0.5"
    assert result["journal"]["realized_pnl"] is None
    with configured.session() as session:
        row = session.get(TradeIdea, ident)
        row.broker_observed_at = now()-timedelta(minutes=3)
        result = review.serialize(row, state=session.get(State, 1))
    assert result["placement_evidence"] == "unresolved_or_stale"
    assert result["journal"]["broker_status"] == "unknown"
    assert result["journal"]["filled_quantity"] is None
    assert result["broker_order"]["last_observed_raw_state"] == "partially_filled"


@pytest.mark.parametrize("terminal,expected", [("expired", "expired"),
    ("partially_filled_rest_cancelled", "partially_filled_then_canceled")])
def test_terminal_readonly_observations_cannot_regress(configured, terminal, expected):
    ident, record = seeded(configured), order()
    record["state"] = terminal
    snapshot(configured, ident, [record])
    result = review.link_order(configured, "synthetic-owner", ident, record["id"])
    assert result["journal"]["broker_status"] == expected
    record["state"] = "confirmed"
    with configured.session() as session:
        with pytest.raises(HTTPException, match="conflicts"):
            review._record_manual_observation(session, session.get(State, 1),
                session.get(TradeIdea, ident), record, "synthetic-owner", now())
