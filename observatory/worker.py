import asyncio
import hashlib
import json
import logging
import resource
import time
import uuid
from datetime import timedelta
import httpx
from sqlalchemy import select, text
from .analysis import analyze
from .broker import ReadOnlyBroker, BrokerError, AuthorizationExpired, Throttled, retry_delay
from .calendar import poll_delay
from .collection import collect
from .db import Database, State, Snapshot, Event, Work, Observation, aware, now
from .oauth import RobinhoodOAuth
from .security import Vault
from .service import start_observation, finalize_observations
from .settings import Settings
from .rate_limit import RequestLimiter
from .market import refresh_quotes
from .worker_session import WorkerBroker

log = logging.getLogger("observatory.worker")


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def position_signature(payload):
    positions = [
        {
            "asset": key,
            **{
                k: x.get(k)
                for k in [
                    "symbol",
                    "option_id",
                    "quantity",
                    "type",
                    "pending_assignment_quantity",
                    "pending_exercise_quantity",
                    "pending_expiration_quantity",
                ]
            },
        }
        for key in ["equity_positions", "option_positions"]
        for x in payload.get(key, [])
    ]
    return sorted(positions, key=lambda x: json.dumps(x, sort_keys=True))


def record_events(session, payload, previous, previous_snapshot_id=None):
    changes = []
    for key in ["equity_orders", "option_orders"]:
        for order in payload.get(key, []):
            event_data = {
                "id": order["id"],
                "state": order["state"],
                "filled": order.get("cumulative_quantity"),
                "updated": order.get("updated_at") or order.get("last_transaction_at"),
            }
            ident = fingerprint({"kind": key, **event_data})
            if session.get(Event, ident) is None:
                # Initial reconciliation establishes a quiet baseline, avoiding a history alert flood.
                session.add(
                    Event(
                        id=ident,
                        kind="order_change",
                        payload=event_data,
                        delivery_state="pending" if previous else "baseline",
                    )
                )
                if previous:
                    changes.append(ident)
    if previous and position_signature(payload) != position_signature(previous):
        # One event per committed transition, so A->B->A->B remains observable.
        ident = fingerprint(
            {"kind": "positions", "previous_snapshot_id": previous_snapshot_id,
             "from": position_signature(previous), "to": position_signature(payload)}
        )
        if session.get(Event, ident) is None:
            session.add(
                Event(
                    id=ident,
                    kind="positions_changed",
                    payload={"detail": "Position quantities or contract events changed"},
                )
            )
            changes.append(ident)
    for contract in payload.get("contracts", []):
        expiry = contract["expiration"]
        days = (__import__("datetime").date.fromisoformat(expiry) - now().date()).days
        if 0 <= days <= 7:
            ident = fingerprint({"kind": "expiration", "contract": contract["id"], "expiration": expiry})
            if session.get(Event, ident) is None:
                session.add(
                    Event(
                        id=ident,
                        kind="expiration",
                        payload={
                            "contract_id": contract["id"],
                            "expiration": expiry,
                            "sellout_at": contract.get("sellout_at"),
                            "assignment_exposure": "unknown",
                        },
                    )
                )
                changes.append(ident)
    return changes


async def heartbeat(db, settings):
    if not settings.heartbeat_url:
        return False
    # URL is a secret. Never include it or exception text in logs.
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(settings.heartbeat_url, content=b"")
        response.raise_for_status()
        with db.session() as s:
            s.get(State, 1).last_heartbeat = now()
        return True
    except Exception:
        log.warning("external_heartbeat_delivery_failed")
        return False


async def notifications(db, settings):
    if not settings.notification_url:
        return
    with db.session() as s:
        rows = s.scalars(
            select(Event).where(Event.delivery_state == "pending").limit(10).with_for_update(skip_locked=True)
        ).all()
        ids = [x.id for x in rows]
        for row in rows:
            row.delivery_state = "sending"
    for ident in ids:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.post(
                    settings.notification_url,
                    headers={
                        "Authorization": f"Bearer {settings.notification_token}",
                        "Idempotency-Key": ident,
                    },
                    json={
                        "id": ident,
                        "title": "Observatory update",
                        "message": "An account or monitoring update is available. Open the dashboard.",
                    },
                )
            response.raise_for_status()
            state = "delivered"
        except Exception:
            # No blind retry after ambiguous delivery: duplicates would be possible without receiver dedupe.
            state = "delivery_unknown"
        with db.session() as s:
            row = s.get(Event, ident)
            row.delivery_state = state
            if state == "delivered":
                row.notified_at = now()


async def run_cycle(db, settings, vault, broker_factory=ReadOnlyBroker, connection=None):
    started = time.monotonic()
    with db.session() as s:
        row = s.scalar(select(State).where(State.id == 1).with_for_update())
        if row.paused or not row.account_verified or (row.next_run and aware(row.next_run) > now()):
            return "idle"
        account = vault.decrypt(row.account_cipher)
        config, generation = dict(row.config), row.generation
        row.last_attempt = now()
        row.status = "reconciling"
        row.next_run = now() + timedelta(seconds=settings.active_poll_seconds)
        job_id = str(uuid.uuid4())
        s.add(Work(id=job_id))
        # Interrupted records from previous process become explicit, not falsely successful.
        for old in s.scalars(select(Work).where(Work.state == "running", Work.id != job_id)):
            old.state = "interrupted"
            old.finished_at = now()
    broker = None
    try:
        token = await RobinhoodOAuth(db, vault, settings).access_token()
        broker = broker_factory(token, account, settings.broker_rpm)
        broker.limiter = RequestLimiter(db, settings.broker_rpm)
        if connection is not None:
            pid = connection.scalar(text("SELECT pg_backend_pid()"))
            connection.commit()
            original = broker.rpc

            async def guarded_rpc(*args, **kwargs):
                if (
                    connection.invalidated
                    or connection.closed
                    or connection.scalar(text("SELECT pg_backend_pid()")) != pid
                ):
                    raise RuntimeError("Leadership lost")
                connection.commit()
                return await original(*args, **kwargs)

            broker.rpc = guarded_rpc
        metrics_before = dict(broker.metrics)
        await broker.initialize()
        payload = await collect(broker, config)
        snap = Snapshot(id=str(uuid.uuid4()), source="robinhood_mcp_live", payload=payload, observed_at=now())
        with db.session() as s:
            row = s.scalar(select(State).where(State.id == 1).with_for_update())
            if row.generation != generation or row.paused:
                s.get(Work, job_id).state = "superseded"
                s.get(Work, job_id).finished_at = now()
                return "superseded"
            previous = s.scalar(
                select(Snapshot)
                .where(Snapshot.source == "robinhood_mcp_live")
                .order_by(Snapshot.observed_at.desc())
            )
            changes = record_events(s, payload, previous.payload if previous else None,
                                    previous.id if previous else None)
            s.add(snap)
            from .manual_orders import reconcile_records
            reconcile_records(s, payload, row.account_cipher, snap.observed_at)
            row.last_success = now()
            row.status = "degraded" if payload.get("warnings") else "monitoring"
            row.failures = 0
            row.checkpoint = {"snapshot_id": snap.id, "reconciled_at": now().isoformat()}
            row.next_run = now() + timedelta(seconds=poll_delay(settings, config, payload, now()))
            work = s.get(Work, job_id)
            work.state, work.finished_at = "succeeded", now()
            work.metrics = {
                **{k: v - metrics_before.get(k, 0) for k, v in broker.metrics.items()},
                "cycle_seconds": round(time.monotonic() - started, 3),
                "max_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
            should_analyze = (
                bool(changes)
                or row.last_analysis is None
                or aware(row.last_analysis) < now() - timedelta(hours=4)
            )
            if should_analyze:
                row.last_analysis = now()
        await heartbeat(db, settings)  # Only after complete validated state has committed.
        await notifications(db, settings)
        with db.session() as s:
            has_observation = s.scalar(select(Observation.id).limit(1))
        if not has_observation:
            start_observation(db, "worker")
        finalize_observations(db)
        if should_analyze:
            result = await analyze(db, settings, snap)
            with db.session() as s:
                s.get(State, 1).analysis_status = result
        return "succeeded"
    except Exception as exc:
        code = exc.code if isinstance(exc, BrokerError) else "monitoring_failed"
        with db.session() as s:
            row = s.scalar(select(State).where(State.id == 1).with_for_update())
            if row.generation != generation or row.paused:
                job = s.get(Work, job_id)
                job.state, job.finished_at = "superseded", now()
                return "superseded"
            row.failures += 1
            row.status = code
            delay = exc.retry_after if isinstance(exc, Throttled) else retry_delay(None, row.failures)
            row.next_run = now() + timedelta(seconds=max(settings.active_poll_seconds, delay))
            if isinstance(exc, AuthorizationExpired):
                row.account_verified = False
                row.paused = True
            job = s.get(Work, job_id)
            job.state, job.finished_at = code, now()
            ident = fingerprint({"kind": code, "since_success": str(row.last_success)})
            if s.get(Event, ident) is None:
                s.add(Event(id=ident, kind="operational_failure", payload={"code": code}))
        await notifications(db, settings)
        log.warning("cycle_failed code=%s", code)
        return code
    finally:
        if broker:
            await broker.close()


async def serve_leader(db, settings, vault, connection):
    """Serialized account and quote work; no concurrent sessions on the lock connection."""
    broker = None
    pid = connection.scalar(text("SELECT pg_backend_pid()"))
    connection.commit()
    try:
        while True:
            with db.session() as s:
                state = s.get(State, 1)
                active = not state.paused and state.account_verified
                account = vault.decrypt(state.account_cipher) if active else None
                generation = state.generation
            if not active:
                if broker:
                    await broker.shutdown()
                    broker = None
                await asyncio.sleep(5)
                continue
            try:
                token = await RobinhoodOAuth(db, vault, settings).access_token()
                if broker is None:
                    broker = WorkerBroker(token, account, settings.broker_rpm)
                    broker.limiter = RequestLimiter(db, settings.broker_rpm)

                async def preflight():
                    if (connection.invalidated or connection.closed
                            or connection.scalar(text("SELECT pg_backend_pid()")) != pid):
                        raise RuntimeError("Leadership lost")
                    connection.commit()
                    with db.session() as s:
                        current = s.get(State, 1)
                        if current.paused or not current.account_verified or current.generation != generation:
                            raise RuntimeError("Monitoring configuration changed")
                    broker.token = await RobinhoodOAuth(db, vault, settings).access_token()

                async def quotes():
                    await refresh_quotes(db, settings, broker)

                broker.token, broker.preflight = token, preflight
                broker.before_account_call = quotes
                # Same physical connection is checked AFTER pacing by preflight. No stacked wrappers.
                result = await run_cycle(db, settings, vault, lambda *args: broker)
                if result == "idle":
                    await broker.initialize()
                    await quotes()
            except AuthorizationExpired:
                with db.session() as s:
                    state = s.get(State, 1)
                    state.paused, state.account_verified, state.status = True, False, "authorization_expired"
                log.warning("worker_authorization_expired")
            await asyncio.sleep(5)
    finally:
        if broker:
            await broker.shutdown()


async def main():
    settings = Settings().validate()
    db, vault = Database(settings.database_url), Vault(settings.encryption_keys)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for name in ["httpx", "httpcore", "sqlalchemy", "mcp"]:
        logging.getLogger(name).setLevel(logging.CRITICAL)
    while True:
        try:
            with db.leader() as connection:
                if connection is not None:
                    await serve_leader(db.bound(connection), settings, vault, connection)
        except Exception:
            # DB unavailable: no heartbeat, no exposure reset, no model request.
            log.warning("worker_cycle_unavailable")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())

