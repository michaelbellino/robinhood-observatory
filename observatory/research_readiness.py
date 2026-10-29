"""Read-only readiness facts; configuration is never proof of successful research.

The caller must authenticate the owner. No credentials, contact address, holdings,
source error text, or model-generated prose are returned by this diagnostic.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json

from sqlalchemy import select

from .db import Analysis, Snapshot, State, aware, now
from .research import VERSION, source_settings


def _positive(value):
    try:
        number = Decimal(str(value))
        return number.is_finite() and number > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


def readiness(db, settings, *, at=None):
    """Describe this process and persisted observations, without activating work.

    In production the web and worker deliberately have different environments.
    A web endpoint MUST NOT treat its own API-key presence as worker readiness.
    """
    at = at or now()
    with db.session() as session:
        state = session.get(State, 1)
        config = dict(state.config or {}) if state else {}
        latest = session.scalar(select(Analysis).where(Analysis.strategy_version == VERSION)
                                .order_by(Analysis.created_at.desc()).limit(1))
        snapshot = session.get(Snapshot, latest.snapshot_id) if latest else None
        report = latest.payload if latest and isinstance(latest.payload, dict) else {}
        current_binding = bool(state and state.account_verified and state.account_binding and snapshot
                               and snapshot.account_binding == state.account_binding
                               and snapshot.generation == state.generation)
        monitor_active = bool(state and not state.paused and state.account_verified and state.account_binding)
        stamp = aware(latest.created_at) if latest else None
        source_config = {key: config.get(key) for key in
                         ("research_sources", "research_sec_user_agent", "research_generation_enabled")}
        config_hash = hashlib.sha256(json.dumps(source_config, sort_keys=True).encode()).hexdigest()
        config_matches = bool(latest and report.get("source_config_hash") == config_hash)
        recent = bool(stamp and at - timedelta(hours=4, minutes=5) <= stamp <= at + timedelta(seconds=5))
        try:
            sources, _ = source_settings(config)
            contact_valid = True
            source_valid = bool(sources)
            symbols = [source.symbol for source in sources]
        except (TypeError, ValueError):
            contact_valid = False
            source_valid = False
            symbols = []
            # Separate invalid source data from a valid contact declaration.
            try:
                source_settings({**config, "research_sources": []})
                contact_valid = True
            except (TypeError, ValueError):
                pass
        model = {
            "configuration_scope": "current_process_only",
            "worker_configuration_verified": False,
            "key_configured": bool(settings.openai_key),
            "model_configured": bool(settings.openai_model),
            "pricing_configured": _positive(settings.ai_input_per_million) and _positive(settings.ai_output_per_million),
            "positive_budget_configured": (_positive(settings.ai_daily_calls) and _positive(settings.ai_daily_usd)
                                           and _positive(settings.ai_monthly_usd)),
            "budget_remaining_verified": False,
        }
        model["configuration_complete"] = all(model[key] for key in
            ("key_configured", "model_configured", "pricing_configured", "positive_budget_configured"))
        errors = report.get("source_errors")
        errors = errors if isinstance(errors, list) else []
        leads = report.get("leads")
        leads = leads if isinstance(leads, list) else []
        content_verified_count = sum(
            1 for lead in leads if isinstance(lead, dict) and lead.get("content_status") == "verified"
            and isinstance(lead.get("content_excerpt"), str) and bool(lead["content_excerpt"].strip())
        )
        generation = report.get("generation")
        generation = generation if isinstance(generation, dict) else {}
        allowed_statuses = {"disabled", "insufficient_source_evidence", "insufficient_market_inputs",
                            "disabled_or_budget_exhausted", "available", "degraded"}
        generation_status = generation.get("status")
        generation_status = generation_status if generation_status in allowed_statuses else "unknown"
        observed = bool(monitor_active and recent and current_binding and config_matches and not errors and source_valid)
        blockers = []
        if not monitor_active:
            blockers.append("verified_unpaused_account_required_by_current_worker")
        if not source_valid:
            blockers.append("valid_nonempty_source_universe_required")
        if not contact_valid:
            blockers.append("declared_sec_contact_required")
        if not latest:
            blockers.append("research_run_not_observed")
        elif not recent or not current_binding or not config_matches:
            blockers.append("current_fresh_research_run_not_observed")
        if errors:
            blockers.append("last_source_pass_reported_errors")
        if config.get("research_generation_enabled") is not True:
            blockers.append("candidate_generation_disabled")
        return {
            "checked_at": at.isoformat(),
            "mode": "research_only_observed" if observed else "not_ready",
            "research_only_observed": observed,
            "actionable_proposals_ready": False,
            "actionable_reason": "Each candidate still requires current mandate, account coverage, quotes, collateral and exit validation.",
            "execution_route": "manual_robinhood",
            "monitor_active": monitor_active,
            "broker_dependency": "Current research cycle runs only after successful authenticated account reconciliation.",
            "sources": {"valid": source_valid, "symbols": symbols, "contact_configured": contact_valid,
                        "maximum_sources": 5, "content_scope": "sec_metadata_with_optional_bounded_primary_excerpts"},
            "generation_enabled": config.get("research_generation_enabled") is True,
            "model": model,
            "last_research": {"observed": bool(latest), "created_at": stamp.isoformat() if stamp else None,
                              "fresh": recent, "current_account_generation": current_binding,
                              "current_source_configuration": config_matches,
                              "source_error_count": len(errors),
                              "content_verified_count": content_verified_count,
                              "source_error_summary": "Source pass reported errors; review authenticated research history." if errors else None,
                              "generation_status": generation_status},
            "blockers": blockers,
            "limitations": ["Optional bounded primary filing excerpts are incomplete and do not establish broad news analysis.",
                            "Broad option-chain discovery is not implemented; existing collected contracts are reused.",
                            "No end-to-end notification receipt is established by this diagnostic.",
                            "Current-process settings do not establish worker configuration or remaining model budget."],
        }
