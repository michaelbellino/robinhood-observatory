# Acceptance evidence — 2026-10-16

## Result

**Local implementation verification passes. Production acceptance is incomplete.** No independently authenticated hosted broker session, production database, external failure delivery, restored production backup or full trading-day run exists yet. The existing ChatGPT connector's successful reads are separate evidence and are never substituted for application credentials.

| # | Required acceptance | Verified evidence | Outstanding gate |
|---:|---|---|---|
| 1 | Hosted auth + authorized live reads | Official metadata; localhost DCR; existing connector account eligibility/read recheck | Supported hosted consent, account binding, lifetime/refresh/restart/revocation |
| 2 | Equities/options, timestamps, unknowns | Connector quote/spec sample reads; synthetic collection/contract tests; adjusted multiplier test | App's live held-position mapping/reconciliation |
| 3 | Restart recovery/pause/checkpoints | Durable local SQLite restart tests; interrupted work handling | PostgreSQL restart test and deployed restart |
| 4 | One scheduler/no duplicate alerts | Event idempotence and delivery ambiguity tests; PG advisory-lock implementation | PostgreSQL concurrent/crash leader tests; real receiver dedupe |
| 5 | Throttle/stale/auth/DB failures | Injected 401/403/429, full Retry-After, malformed/absent data, stale state retention, DB-failure no heartbeat | Live service fault drills |
| 6 | Reject unauthorized dashboard/MCP | 401 anonymous; 403 read→control; wrong owner; CSRF/Origin; DNS-rebinding; sanitized error tests | Deployed direct/tunnel rejection checks |
| 7 | Monitor without model | Worker succeeds with AI disabled/failing/capped; no repeated unreserved calls | Live prolonged observation |
| 8 | External stopped/stalled detection | Success-only ping and failed-delivery tests | External account/check/channel setup and stopped-worker DOWN/UP receipt |
| 9 | Backup restoration | Consistent-snapshot encrypted export and guarded hash-verified restore implemented; target guard tests | Real PostgreSQL round-trip (CI gate) + actual deployment isolated restore |
| 10 | No mutations/no source/log secrets | Strict allowlist tests; mutation/preview routes absent; source scan; synthetic error-log redaction | Repeat scan/review before publication and inspect redacted production logs |

## Retained files

- `evidence/local-tests.xml`: **89 passed, 5 skipped**, local runtime. Skips are the real PostgreSQL leadership/restart/migration/backup-restore gates, each explicitly marked. No hidden pass substitution.
- `evidence/dependency-audit.json`: locked runtime dependency scan, no known advisories reported at inspection time. This is not a security guarantee.
- `evidence/broker-public-metadata-2026-10-16.json`: unauthenticated official server metadata, no tokens.
- `docs/connector-metadata-2026-10-16.json`: tool declarations inspected from the existing integration; describes advertised support, not successful execution.
- `evidence/local-measurements.json`: empty-app HTTP liveness timing and explicit limits of resource/polling measurement.
- CI workflow: disposable PostgreSQL 17, real lock/restart/migration/restore tests, typecheck, lint, scan and dependency audit. Not run remotely yet because the GitHub repository has not been created.

Alembic migration upgrade and schema-drift check passed locally using SQLite only. The Railway IaC declaration typechecked with the locked SDK. JavaScript syntax check passed. Docker image digests were checked against the official registry, but this environment has no Docker daemon; image build/Compose execution remain unverified.

## Polling and measurements

Application pacing is at most six broker MCP HTTP requests/minute across web and worker, persisted in PostgreSQL; the default active delay is 180 seconds **after** a successful cycle, off-hours 1,800 seconds after success. A cycle includes initialization/tool discovery, account recheck, portfolio, all position/order pages and bounded quote batches. Therefore actual interval is cycle duration + configured delay, not exactly three minutes. Large holdings/pagination reduce cadence. No tick streaming or response-time guarantee is claimed.

Tests measure reserved delays across independent limiter instances/restart and full Retry-After preservation. These are deterministic/simulated tests, not a live permitted-rate experiment. No published broker quota was found; don't increase pacing without broker guidance. Real request counts/throttles/cycle time/RSS will be persisted in Work records.

The empty local app answered 20 liveness requests with mean 0.976 ms and maximum 3.396 ms in this runtime. These are local smoke measurements, not internet latency, database-load or market-session reliability evidence. Production CPU/RAM/egress and actual broker polling remain unmeasured. Runtime process isolation prevented a trustworthy direct RSS sample; it is intentionally not reported as application memory.

## Observation and UI

Full-session observation is **pending, not started**. The first completed independently authenticated live cycle schedules the next complete XNYS reference session. Stored observation evidence records successful/failed cycles and maximum gap against a 600-second criterion. Instrument-specific options hours require separate coverage once verified. A pending or short smoke result must never be described as a completed session.

The responsive dashboard source and HTTP shell work locally; the cloud browser refused localhost with `ERR_BLOCKED_BY_CLIENT`. Desktop/mobile visual review is pending. No callable new-Canvas creation surface was available in this session; the repository remains the design/source record. No screenshot or mock values are represented as live data.
