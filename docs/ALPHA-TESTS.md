# Operational alpha and paper acceptance

This suite validates infrastructure and hypothetical calculations. No trading strategy, order execution route, funded pilot or profitability claim is included.

## Reproducible checks

Run `uv sync --frozen`, `uv run pytest -q`, `uv run ruff check .` and `uv run python scripts/secret_scan.py`. Real PostgreSQL acceptance runs in CI with an isolated disposable database; local skips are explicit. CI verifies lock contention/crash recovery, persisted pause/checkpoints, migrations and an encrypted backup/restore with per-table hashes. These are not production backup results.

The alpha regression tests exercise fresh trades with stale/unknown/future-dated bid/ask quotes, nonpositive/crossed markets, malformed option structures, invalid token lifetimes, malformed refresh responses, metadata-only refresh auditing, bounded quote evidence retention, observation idempotence and unauthorized scheduling.

Paper cost estimates require both bid and ask source times no older than 60 seconds, allowing at most five seconds of clock skew. Last-trade time and receipt time never substitute for them. All estimates remain unfilled, including when a limit or midpoint is touched. A valid structural label does not establish buying power, assignment risk, maximum loss or broker execution support.

## Five-session operational soak

Authenticated control request: `POST /api/observation?sessions=5`. The service schedules five upcoming complete XNYS reference sessions, reusing existing records on repeated requests. This calendar is not an assertion about every option's trading hours. Production approval requires separate contract-specific coverage.

The initial operational thresholds are a maximum 600-second account synchronization gap, maximum 120-second quote sampling gap for each observed instrument, no nonfresh/missing/invalid sampled books, and no failed account cycles. These are engineering acceptance thresholds, not trading latency guarantees. A missing quote sample set is incomplete. Retain source age, sampling gaps, failure counts and acceptance scope. Quotes are sampled observations, not proof that all intervening market events were seen.

The first full session provides baseline evidence. Five sessions are an initial operational soak, not sufficient evidence for strategy performance. Session evidence is finalized only after the close. Quote-quality samples contain no prices or account values, retain 14 days, and are separate from notifications. Session aggregates remain durable.

## Closed-market public-feed probe

`uv run python -m scripts.paper_feed_probe --seconds 120 --output /private/paper-probe.json`

This bounded, optional developer command uses the public Coinbase Exchange BTC/USD ticker WebSocket, with two connections to exercise reconnect behavior. It needs no credentials and has no account/order methods. The websocket library is a pinned development dependency; the production Robinhood collector remains unchanged. Record source/event times, receipt gaps, duplicate/out-of-order rejection and errors. Ticker sequence jumps are not automatically lost ticker messages: the channel is a filtered stream.

Coinbase ticker match timestamps are not treated as separately documented bid/ask timestamps. Paper test intents remain unfilled, with unknown final costs when timestamps, fees or execution assumptions are unavailable. A crypto feed does not validate Robinhood equity/options prices, liquidity, entitlements or execution. The probe does not run perpetually after its bounded process exits.

Primary source: https://docs.cdp.coinbase.com/exchange/websocket-feed/channels

## Deployment gates requiring their own evidence

- Actual broker token renewal/rotation, controlled revocation and recovery. Authenticated owner diagnostics expose only expiry/lifetime/refresh-event metadata, never credential values. Do not manufacture an expiry or revoke live credentials just to obtain a passing test.
- Production backup schedule, isolated production restore and measured recovery time/data-loss window. A fixture restore does not satisfy this gate.
- An external dead-man check and verified receiver, with a real stopped/stalled-worker DOWN/UP drill and deduplicated delivery.
- Actual ChatGPT-originated authenticated status/control calls through the enrolled connection.
- Live held-options and complete relevant order coverage. Empty positions do not validate an options portfolio.
- A separately agreed methodology, historical/out-of-sample evaluation and forward paper assessment.
- Separate execution safeguards and explicit permission before any real-money phase. Monitoring pause neither cancels orders nor closes positions.
