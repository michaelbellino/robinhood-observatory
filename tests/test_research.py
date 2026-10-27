"""Deterministic paper research fixtures; no brokerage data or real ticker thesis."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
import httpx
import pytest
from pydantic import ValidationError
from observatory.research import (ResearchCandidate, SECSource, SECSubmissions, SECBackoff,
                                  compare, loss_envelope, screen, source_settings)

AT = datetime(2026, 10, 27, 15, tzinfo=timezone.utc)


def leg(right="put", strike="100", side="buy", ident="synthetic-contract", **contract_changes):
    return {"contract": {"id": ident, "underlying": "TEST", "expiration": "2026-11-06",
        "strike": strike, "right": right, "multiplier": "100", "deliverable": "100 shares",
        "exercise_style": "american", "settlement": "physical", "observed_at": AT,
        **contract_changes}, "quote": {"instrument_id": ident, "bid": "0.50", "ask": "0.60",
        "bid_at": AT, "ask_at": AT, "received_at": AT, "quoted_at": AT,
        "volume": 50, "open_interest": 100, "delta": "0.2", "gamma": "0.01", "theta": "-0.01",
        "vega": "0.03", "implied_volatility": "0.3"}, "side": side, "quantity": 1,
        "position_effect": "open"}


def candidate(setup="long_put", **changes):
    legs = [leg()]
    if setup == "long_call":
        legs = [leg(right="call")]
    elif setup == "credit_spread":
        legs = [leg(side="sell", strike="100", ident="short"), leg(strike="99", ident="long")]
    elif setup == "covered_call":
        legs = [leg(right="call", side="sell")]
    elif setup == "cash_secured_put":
        legs = [leg(side="sell")]
    close = "sell_to_close" if setup.startswith("long_") else "buy_to_close"
    basis = "spread_value" if setup == "credit_spread" else "option_premium"
    value = {"id": uuid4(), "setup": setup, "symbol": "TEST", "legs": legs,
        "evidence": [{"id": "source-a", "url": "https://example.com/filing",
            "publisher": "Synthetic issuer", "title": "Synthetic filing source",
            "kind": "filing", "claim": "A synthetic issuer released a synthetic filing.",
            "published_at": AT-timedelta(hours=1), "retrieved_at": AT, "retrieval_status": "verified"}],
        "thesis": "An explicitly synthetic and unproven research hypothesis.",
        "counterargument": "The hypothetical event may already be fully priced into the market.",
        "invalidation": "The hypothetical catalyst may fail or be contradicted by later evidence.",
        "priced_in_assessment": "No pricing edge has been independently established in this fixture.",
        "catalyst_at": AT+timedelta(days=1), "catalyst_timing_uncertainty": "This is a synthetic future event.",
        "volatility_comparison": "Synthetic volatility estimate, no proven edge.",
        "skew_assessment": "Synthetic skew remains uncertain.", "liquidity_assessment": "Bid and ask are hypothetical.",
        "event_risk": "Synthetic event can move either way.", "entry_limit": "0.50", "time_in_force": "gfd",
        "created_at": AT, "expires_at": AT+timedelta(minutes=15), "fees_usd": "0.50", "slippage_usd": "1",
        "execution_assumptions": "Hypothetical limit, no assumed fill or midpoint execution.",
        "portfolio_effect": "Synthetic exposure is not proof of actual account holdings.",
        "assignment_and_exercise": "Assignment can create stock exposure and capital obligations.",
        "expiration_early_assignment_ex_dividend_pin": "Expiry, early assignment and pin risk require manual attention.",
        "exits": [
            {"purpose": "take_profit", "basis": basis, "threshold": "0.20", "condition": "Review the hypothetical closing price.",
             "action": close, "mechanism": "alert_requires_owner_action", "justification": "Scenario-based hypothesis to test, not universal percentages.",
             "failure_modes": "Alerts require owner action and fills remain uncertain."},
            {"purpose": "loss_or_invalidation", "basis": "news_event", "condition": "A contradictory source invalidates the hypothesis.",
             "action": "review_and_act", "mechanism": "alert_requires_owner_action", "justification": "Use the counterargument as a testable invalidation.",
             "failure_modes": "A news event can gap before manual action."},
            {"purpose": "time_exit", "basis": "time", "deadline": AT+timedelta(days=2), "condition": "Review before the stated holding deadline.",
             "action": "review_and_act", "mechanism": "alert_requires_owner_action", "justification": "Close or reassess before synthetic catalyst resolves.",
             "failure_modes": "A manual exit may fail in an illiquid market."}],
        "alternative_long_or_premium": "Compare a long option against this synthetic premium-selling setup.",
        "no_trade_alternative": "No trade is appropriate when evidence or execution is insufficient.",
        "evidence_mode": "live_observation"}
    if setup == "covered_call":
        value.update(underlying_reference_price="98", underlying_reference_at=AT)
    value.update(changes)
    return ResearchCandidate.model_validate(value)


def approved(value=None):
    return screen(value or candidate(), AT, {"allowed": True, "mandate_id": "synthetic", "snapshot_id": "synthetic"}, {"source-a"})


def test_default_does_not_activate_or_order():
    result = screen(candidate(), AT)
    assert result["outcome"] == "no_trade"
    assert "confirmed_account_mandate_and_portfolio_required" in result["reasons"]
    assert "independent_source_verification_required" in result["reasons"]
    assert result["broker_order"] is None and result["fill_status"] == "unfilled"


@pytest.mark.parametrize("setup,loss,collateral", [
    ("long_put", "51.50", "51.50"), ("long_call", "51.50", "51.50"),
    ("credit_spread", "51.50", "51.50"), ("cash_secured_put", "9951.50", "10000"),
    ("covered_call", "9751.50", "0")])
def test_explicit_cashflow_loss_and_full_assignment(setup, loss, collateral):
    envelope = loss_envelope(candidate(setup))
    assert Decimal(envelope["max_loss_usd"]) == Decimal(loss)
    assert Decimal(envelope["collateral_required_usd"]) == Decimal(collateral)
    assert envelope["realized_profit_usd"] is None


def test_credit_is_not_profit_and_risk_gate_still_required():
    result = screen(candidate("cash_secured_put"), AT)
    assert result["outcome"] == "no_trade"
    assert result["envelope"]["assignment_obligation_usd"] == "10000"
    assert Decimal(result["envelope"]["max_gain_usd"]) == Decimal("48.50")


def test_source_claim_cannot_self_certify_retrieval():
    result = screen(candidate(), AT, {"allowed": True})
    assert result["outcome"] == "no_trade" and "independent_source_verification_required" in result["reasons"]


def test_complete_supplied_proof_is_only_a_manual_unfilled_proposal():
    result = approved()
    assert result["outcome"] == "proposal_for_manual_review"
    assert result["unavailable_metrics"] == ["realized_volatility"]
    assert result["execution_route"] == "manual_robinhood" and result["broker_order"] is None


@pytest.mark.parametrize("minutes", [-1, 20])
def test_expired_or_future_candidate_needs_new_review(minutes):
    value = candidate(created_at=AT+timedelta(minutes=minutes), expires_at=AT+timedelta(minutes=minutes+1))
    assert "proposal_expired_or_future_dated" in approved(value)["reasons"]


def test_stale_book_and_unknown_liquidity_fail_closed():
    data = candidate().model_dump()
    data["legs"][0]["quote"].update(bid_at=AT-timedelta(minutes=2), volume=None)
    result = approved(ResearchCandidate.model_validate(data))
    assert "leg_0_quote_not_fresh" in result["reasons"]
    assert "leg_0_liquidity_unavailable" in result["reasons"]


def test_rumor_and_retrieval_failure_cannot_be_catalyst_proof():
    data = candidate().model_dump()
    data["evidence"][0].update(kind="rumor", retrieval_status="failed")
    result = approved(ResearchCandidate.model_validate(data))
    assert "confirmed_primary_catalyst_missing" in result["reasons"]
    assert "source_retrieval_unverified" in result["reasons"]


def test_missing_contract_metadata_is_not_assumed():
    data = candidate().model_dump()
    data["legs"][0]["contract"]["deliverable"] = None
    result = approved(ResearchCandidate.model_validate(data))
    assert "risk_envelope_unavailable" in result["reasons"]


def test_inconsistent_contract_quote_rejected():
    data = candidate().model_dump()
    data["legs"][0]["quote"]["instrument_id"] = "another-contract"
    with pytest.raises(ValidationError):
        ResearchCandidate.model_validate(data)


def test_spread_orientation_and_credit_over_width_rejected():
    data = candidate("credit_spread").model_dump()
    data["legs"][0]["contract"]["strike"] = "98"
    with pytest.raises(ValidationError):
        ResearchCandidate.model_validate(data)
    assert "risk_envelope_unavailable" in approved(candidate("credit_spread", entry_limit="1.01"))["reasons"]


def test_exits_need_basis_correct_close_and_broker_proof():
    data = candidate("cash_secured_put").model_dump()
    data["exits"][0]["action"] = "sell_to_close"
    with pytest.raises(ValidationError):
        ResearchCandidate.model_validate(data)
    data = candidate().model_dump()
    data["exits"][0].update(mechanism="resting_broker_order", broker_support_source=None)
    with pytest.raises(ValidationError):
        ResearchCandidate.model_validate(data)
    data = candidate().model_dump()
    data["exits"][0]["threshold"] = None
    with pytest.raises(ValidationError):
        ResearchCandidate.model_validate(data)


def test_unsupported_exit_and_zero_net_credit_do_not_activate():
    data = candidate().model_dump()
    data["exits"][0]["mechanism"] = "unverified"
    assert "exit_feasibility_unverified" in approved(ResearchCandidate.model_validate(data))["reasons"]
    assert "costs_consume_expected_premium" in approved(candidate("credit_spread", fees_usd="50"))["reasons"]


def test_compare_does_not_rank_missing_alternatives_or_claim_return():
    result = compare([candidate(), candidate("credit_spread")], AT)
    assert result["families_compared"] == ["long_options", "premium_selling"]
    assert result["no_trade_valid"] and result["live_order_count"] == 0
    assert all(x["outcome"] == "no_trade" for x in result["results"])
    with pytest.raises(ValueError):
        compare([candidate() for _ in range(21)], AT)


@pytest.mark.parametrize("config", [{}, {"research_sources": [{"symbol": "TEST", "cik": "http://localhost"}],
    "research_sec_user_agent": "Synthetic bot test@example.com"}, {"research_sec_user_agent": "No contact"}])
def test_source_settings_do_not_infer_contacts_or_fetch_arbitrary_urls(config):
    with pytest.raises(ValueError):
        source_settings(config)


def sec_body(**changes):
    value = {"cik": "123", "tickers": ["TEST"], "filings": {"recent": {
        "accessionNumber": ["0000000123-26-000001"], "form": ["8-K"], "filingDate": ["2026-10-27"],
        "acceptanceDateTime": [(AT-timedelta(hours=1)).isoformat()]}}}
    value.update(changes)
    return value


@pytest.mark.asyncio
async def test_sec_acquisition_fixed_host_and_distinct_dates():
    async def handle(request):
        assert request.url.host == "data.sec.gov"
        assert request.url.path == "/submissions/CIK0000000123.json"
        assert request.headers["user-agent"] == "Synthetic Observatory test@example.com"
        return httpx.Response(200, json=sec_body())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        rows = await SECSubmissions().fetch(SECSource(symbol="TEST", cik="123"),
            "Synthetic Observatory test@example.com", AT, client)
    assert rows[0]["filing_date"] == "2026-10-27"
    assert rows[0]["accepted_at"] != rows[0]["retrieved_at"]
    assert "not a trade thesis" in rows[0]["limitation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 403, 500])
async def test_sec_error_or_redirect_is_not_followed(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status,
            headers={"Location": "http://127.0.0.1/private"}))) as client:
        with pytest.raises(ValueError):
            await SECSubmissions().fetch(SECSource(symbol="TEST", cik="123"), "App test@example.com", AT, client)


@pytest.mark.asyncio
async def test_sec_honors_backoff_and_identity():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429,
            headers={"Retry-After": "1800"}))) as client:
        with pytest.raises(SECBackoff) as error:
            await SECSubmissions().fetch(SECSource(symbol="TEST", cik="123"), "App test@example.com", AT, client)
        assert error.value.seconds == 1800
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
            json=sec_body(tickers=["OTHER"])))) as client:
        with pytest.raises(ValueError):
            await SECSubmissions().fetch(SECSource(symbol="TEST", cik="123"), "App test@example.com", AT, client)


class FakeGenerator:
    def __init__(self, value):
        self.value, self.calls = value, 0

    async def generate(self, settings, context):
        self.calls += 1
        return self.value


def model_context(value):
    return {"sources": [x.model_dump(mode="json") for x in value.evidence],
            "contracts": [x.contract.model_dump(mode="json") for x in value.legs],
            "quotes": [x.quote.model_dump(mode="json") for x in value.legs]}


def enable_model(settings):
    settings.openai_key, settings.openai_model = "synthetic-not-a-real-key", "synthetic-model"
    settings.ai_daily_calls = 2
    settings.ai_daily_usd = settings.ai_monthly_usd = "1"
    settings.ai_input_per_million = settings.ai_output_per_million = "1"


@pytest.mark.asyncio
async def test_generation_default_budget_zero_makes_no_call(db, settings):
    from observatory.research import generate_candidates
    adapter = FakeGenerator(None)
    result = await generate_candidates(db, settings, model_context(candidate()), adapter)
    assert result["status"] == "disabled_or_budget_exhausted" and adapter.calls == 0


@pytest.mark.asyncio
async def test_generated_candidates_must_preserve_evidence_contracts_and_quotes(db, settings):
    from observatory.research import generate_candidates
    from observatory.db import AICost
    from sqlalchemy import select
    enable_model(settings)
    value = candidate()
    adapter = FakeGenerator({"conclusion": "candidates", "reason": "Synthetic hypothesis for deterministic validation.",
                             "candidates": [value]})
    result = await generate_candidates(db, settings, model_context(value), adapter)
    assert result["status"] == "available" and len(result["candidates"]) == 1
    assert result["candidates"][0] == value
    with db.session() as s:
        row = s.scalar(select(AICost))
        assert row.state == "completed" and row.reserved_usd > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["source", "quote", "contract"])
async def test_generator_hallucination_fails_and_retains_cost(db, settings, changed):
    from observatory.research import generate_candidates
    from observatory.db import AICost
    from sqlalchemy import select
    enable_model(settings)
    value = candidate()
    data = value.model_dump()
    if changed == "source":
        data["evidence"][0]["claim"] = "An invented claim that is not in the supplied source."
    elif changed == "quote":
        data["legs"][0]["quote"]["bid"] = "0.59"
    else:
        data["legs"][0]["contract"]["strike"] = "101"
    adapter = FakeGenerator({"conclusion": "candidates", "reason": "Synthetic hypothesis for deterministic validation.",
                             "candidates": [data]})
    result = await generate_candidates(db, settings, model_context(value), adapter)
    assert result["status"] == "degraded" and not result["candidates"]
    with db.session() as s:
        row = s.scalar(select(AICost))
        assert row.state == "uncertain_or_failed" and row.reserved_usd > 0


@pytest.mark.asyncio
async def test_shared_budget_suppresses_second_call(db, settings):
    from observatory.research import generate_candidates
    enable_model(settings)
    settings.ai_daily_calls = 1
    adapter = FakeGenerator({"conclusion": "no_trade", "reason": "Filing metadata alone cannot establish a supported hypothesis.", "candidates": []})
    context = model_context(candidate())
    assert (await generate_candidates(db, settings, context, adapter))["status"] == "available"
    assert (await generate_candidates(db, settings, context, adapter))["status"] == "disabled_or_budget_exhausted"
    assert adapter.calls == 1


def test_generation_context_omits_account_financials_and_raw_orders():
    from types import SimpleNamespace
    from observatory.research import generation_context
    value = candidate()
    snap = SimpleNamespace(payload={"account_number": "DO-NOT-TRANSMIT", "portfolio": {"cash": "999999"},
        "equity_orders": [{"secret": "PRIVATE"}], "option_positions": [{"quantity": "999"}],
        "contracts": [x.contract.model_dump(mode="json") for x in value.legs],
        "option_quotes": [x.quote.model_dump(mode="json") for x in value.legs]})
    lead = {"symbol": "TEST", "source_id": "sec:synthetic", "url": "https://data.sec.gov/submissions/CIK0000000123.json",
            "form": "8-K", "claim": "Synthetic metadata only, not a trade hypothesis.", "accepted_at": AT.isoformat(),
            "retrieved_at": AT.isoformat(), "retrieval_status": "verified"}
    context = generation_context(snap, [lead])
    assert set(context) == {"mode", "sources", "contracts", "quotes", "limitation"}
    assert "DO-NOT-TRANSMIT" not in str(context) and "PRIVATE" not in str(context)


@pytest.mark.asyncio
async def test_worker_source_cycle_persists_no_trade_and_respects_cooldown(configured, settings):
    from types import SimpleNamespace
    from observatory.research import run_research_cycle, VERSION
    from observatory.db import Analysis, State, now
    from sqlalchemy import select
    with configured.session() as session:
        state = session.get(State, 1)
        state.account_binding = "synthetic-binding"
        state.config = {"research_sources": [{"symbol": "TEST", "cik": "123"}],
                        "research_sec_user_agent": "Synthetic Observatory test@example.com"}
    class Sources:
        calls = 0
        async def fetch(self, source, user_agent, at):
            self.calls += 1
            return []
    adapter = Sources()
    snapshot = SimpleNamespace(id=str(uuid4()), account_binding="synthetic-binding", generation=0,
                               observed_at=now(), payload={})
    assert await run_research_cycle(configured, settings, snapshot, adapter) == "research_only"
    assert await run_research_cycle(configured, settings, snapshot, adapter) == "not_due"
    assert adapter.calls == 1
    with configured.session() as session:
        row = session.scalar(select(Analysis).where(Analysis.strategy_version == VERSION))
        assert row.payload["outcome"] == "no_trade" and row.payload["live_order_count"] == 0
        assert row.payload["gate"]["allowed"] is False


def publish_fixture():
    """Current-time synthetic candidate with broker-shaped UUIDs, never a live order."""
    from observatory.db import now
    at = now()
    data = candidate().model_dump()
    data.update(created_at=at, expires_at=at+timedelta(minutes=15), catalyst_at=at+timedelta(days=1))
    data["evidence"][0].update(published_at=at-timedelta(hours=1), retrieved_at=at)
    ident = str(uuid4())
    data["legs"][0]["contract"].update(id=ident, observed_at=at, expiration=(at+timedelta(days=7)).date())
    data["legs"][0]["quote"].update(instrument_id=ident, received_at=at, quoted_at=at, bid_at=at, ask_at=at)
    data["exits"][2]["deadline"] = at+timedelta(days=2)
    return ResearchCandidate.model_validate(data)


def publication_report(value, outcome="proposal_for_manual_review"):
    return {"leads": [{"source_id": "source-a", "retrieval_status": "verified"}],
            "comparison": {"results": [{"outcome": outcome, "candidate": value.model_dump(mode="json")}]}}


@pytest.mark.parametrize("report", [{}, {"comparison": {"results": []}}])
def test_publication_no_candidates_creates_no_ideas_or_notifications(configured, report):
    from observatory.research import publish_assessed_candidates
    from observatory.db import TradeIdea, Event
    from sqlalchemy import select, func
    assert publish_assessed_candidates(configured, report) == []
    with configured.session() as session:
        assert session.scalar(select(func.count()).select_from(TradeIdea)) == 0
        assert session.scalar(select(func.count()).select_from(Event)) == 0


def test_no_trade_report_never_calls_publication(configured, monkeypatch):
    from observatory.research import publish_assessed_candidates
    from observatory import trade_review
    def forbidden(*args, **kwargs):
        raise AssertionError("No-trade assessment must never call create")
    monkeypatch.setattr(trade_review, "create", forbidden)
    assert publish_assessed_candidates(configured, publication_report(publish_fixture(), "no_trade")) == []


def test_publisher_cannot_bypass_missing_mandate_or_worker_evidence(configured, monkeypatch):
    from observatory.research import publish_assessed_candidates
    from observatory import mandate
    value = publish_fixture()
    report = publication_report(value)
    assert publish_assessed_candidates(configured, report) == []  # Real gate denies unconfirmed mandate.
    monkeypatch.setattr(mandate, "live_proposal_gate", lambda *a: {
        "allowed": True, "mandate_id": "synthetic-mandate", "snapshot_id": "synthetic-snapshot", "risk": {}})
    assert publish_assessed_candidates(configured, report) == []  # Passing a dict is not persisted worker proof.


def test_persisted_assessment_revalidates_and_publishes_once(configured, monkeypatch):
    from observatory.research import publish_assessed_candidates, VERSION
    from observatory import mandate
    from observatory.db import Analysis, TradeIdea, Event
    from sqlalchemy import select, func
    value = publish_fixture()
    report = publication_report(value)
    gate = {"allowed": True, "mandate_id": "synthetic-mandate", "snapshot_id": "synthetic-snapshot", "risk": {}}
    monkeypatch.setattr(mandate, "live_proposal_gate", lambda *a: gate)
    with configured.session() as session:
        session.add(Analysis(id=str(uuid4()), snapshot_id="synthetic-snapshot", payload=report,
                             model="synthetic-test", strategy_version=VERSION))
    assert publish_assessed_candidates(configured, report) == [str(value.id)]
    # A restart/retry sees the same candidate and never adds another proposal/notification.
    assert publish_assessed_candidates(configured, report) == [str(value.id)]
    with configured.session() as session:
        assert session.scalar(select(func.count()).select_from(TradeIdea)) == 1
        assert session.scalar(select(func.count()).select_from(Event).where(Event.kind == "trade_idea")) == 1
        row = session.get(TradeIdea, str(value.id))
        assert row.broker_order_id is None and row.status == "awaiting_review"
        assert row.payload["mandate_id"] == "synthetic-mandate"
