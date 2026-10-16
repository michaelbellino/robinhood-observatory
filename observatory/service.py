import uuid
from datetime import timedelta
from sqlalchemy import select
from .db import State, Snapshot, Event, Analysis, PaperProposal, Audit, Observation, Work, aware, now
from .domain import Quote
from .calendar import next_full_session


def status(db):
    with db.session() as s:
        row = s.get(State, 1)
        if row is None:
            raise ValueError("Migrations required")
        snap = s.scalar(
            select(Snapshot)
            .where(Snapshot.source == "robinhood_mcp_live")
            .order_by(Snapshot.observed_at.desc())
        )
        success = aware(row.last_success)
        age = (now() - success).total_seconds() if success else None
        next_run = aware(row.next_run)
        stale = success is None or next_run is None or now() > next_run + timedelta(seconds=120)
        payload = snap.payload if snap else None
        freshness = []
        if payload:
            freshness = [
                {"instrument": q["instrument_id"], "state": Quote.model_validate(q).freshness(now())}
                for q in payload.get("quotes", []) + payload.get("option_quotes", [])
            ]
        return {
            "mode": "observe_only",
            "real_trading": "disabled",
            "account": row.account_label,
            "account_verified": row.account_verified,
            "paused": row.paused,
            "last_success": success.isoformat() if success else None,
            "data_age_seconds": age,
            "health": "stale" if stale and not row.paused else row.status,
            "authorization": row.status
            if row.status == "authorization_expired"
            else ("verified_at_last_sync" if success else "unverified"),
            "analysis": row.analysis_status,
            "next_run": next_run.isoformat() if next_run else None,
            "snapshot": payload,
            "quote_freshness": freshness,
            "cost_estimate": {
                "railway_monthly_usd": [10, 25],
                "type": "estimate_not_measured",
                "ai_default_usd": 0,
            },
            "config": row.config,
            "display_timezone": "America/New_York",
        }


def history(db, kind, limit=50):
    models = {
        "events": Event,
        "analysis": Analysis,
        "paper": PaperProposal,
        "work": Work,
        "observations": Observation,
    }
    if kind not in models:
        raise ValueError("Unsupported history type")
    model = models[kind]
    with db.session() as s:
        key = {
            "events": Event.occurred_at,
            "analysis": Analysis.created_at,
            "paper": PaperProposal.created_at,
            "work": Work.started_at,
            "observations": Observation.starts_at,
        }[kind]
        rows = s.scalars(select(model).order_by(key.desc()).limit(min(max(limit, 1), 100))).all()
        return [{c.name: getattr(x, c.name) for c in model.__table__.columns} for x in rows]


def change_pause(db, actor, paused, request_id):
    ident = str(uuid.UUID(request_id))
    with db.session() as s:
        row = s.scalar(select(State).where(State.id == 1).with_for_update())
        existing = s.get(Audit, ident)
        if existing:
            if existing.action != ("pause" if paused else "resume") or existing.actor != actor:
                raise ValueError("Idempotency key reused with different request")
            return {"paused": existing.details["paused"], "replayed": True}
        if not paused and (not row.account_verified or not row.account_cipher):
            raise ValueError("Authenticate and explicitly select an account before resuming")
        row.paused = paused
        row.generation += 1
        row.status = "paused" if paused else "reconciling"
        row.next_run = now()
        s.add(
            Audit(id=ident, actor=actor, action="pause" if paused else "resume", details={"paused": paused})
        )
        return {"paused": paused, "replayed": False}


def start_observation(db, actor):
    start, end = next_full_session(now())
    ident = str(uuid.uuid4())
    with db.session() as s:
        existing = s.scalar(select(Observation).where(Observation.status == "pending"))
        if existing:
            return existing.id
        s.add(
            Observation(
                id=ident,
                starts_at=start,
                ends_at=end,
                evidence={
                    "calendar": "XNYS",
                    "coverage": "reference equity session; options hours may differ",
                },
            )
        )
        s.add(
            Audit(
                id=str(uuid.uuid4()),
                actor=actor,
                action="start_observation",
                details={"observation_id": ident},
            )
        )
    return ident


def finalize_observations(db):
    with db.session() as s:
        for observation in s.scalars(
            select(Observation).where(Observation.status == "pending", Observation.ends_at < now())
        ):
            start, end = aware(observation.starts_at), aware(observation.ends_at)
            records = s.scalars(
                select(Work).where(Work.started_at >= start, Work.started_at <= end).order_by(Work.started_at)
            ).all()
            successes = [aware(x.finished_at) for x in records if x.state == "succeeded" and x.finished_at]
            points = [start] + successes + [end]
            max_gap = max((b - a).total_seconds() for a, b in zip(points, points[1:]))
            good = len(successes) > 0 and all(x.state == "succeeded" for x in records) and max_gap <= 600
            observation.status = "passed" if good else "failed_or_incomplete"
            observation.evidence = {
                **observation.evidence,
                "successful_cycles": len(successes),
                "max_gap_seconds": max_gap,
                "gap_limit_seconds": 600,
                "failed_cycles": sum(x.state != "succeeded" for x in records),
            }
