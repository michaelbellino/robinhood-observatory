# Acceptance verification — October 16, 2026

Implementation tests pass; they do not establish any particular deployment's complete production acceptance. Account metadata, live measurements and operator evidence are retained privately.

| Requirement | Reproducible implementation evidence | Deployment evidence required privately |
|---|---|---|
| Independent auth and reads | Metadata validation, PKCE/native enrollment, encrypted deposit, scoped account checks | Actual consent, eligibility, lifetime/refresh/revocation |
| Equity/options correctness | Schema, multiplier, timestamp, missing-data and structure tests | Account-specific held-instrument reconciliation |
| Restart recovery | Real PostgreSQL pause/checkpoint, interrupted work and recovery tests | Restart with live credentials/checkpoints |
| Single scheduler and dedupe | PG leadership/crash tests and event idempotence | Deploy overlap and actual receiver behavior |
| Failure safety | 401/403/429, Retry-After, malformed/stale data and DB failure tests | Selected live operational drills |
| Endpoint authorization | Owner, scope, CSRF/Origin and MCP boundary tests | Deployed API/MCP rejection and intended client connection |
| Monitoring without AI | Disabled/failing/capped-model tests | Prolonged operation with analysis unavailable |
| External outage detection | Success-only heartbeat and failed delivery tests | Actual external stopped/stalled DOWN/UP receipt |
| Backup restore | Encrypted isolated PostgreSQL restore and manifest verification in CI | Scheduled production backup and isolated production restore |
| No mutations/secrets | Strict read allowlist and source/error-log tests | Private deployment inspection |

## Current tests

Alpha release `6c1834f64e5a93c782704d62754e52df036a0155`: [179 passed, no skips in CI](https://github.com/michaelbellino/robinhood-observatory/actions/runs/38984854955). The exact backup container also completed an isolated synthetic restore with all 14 table hashes matching. Local: 172 passed, seven explicit PostgreSQL skips. Runtime and operations dependency audits and source scanning passed. See ALPHA-TESTS.md and BACKUP-JOB.md.

Historical quote release:

Commit `c902a4c31d40a6b4798428a6581eb1dec410a9ba`: [156 passed, no skips in CI](https://github.com/michaelbellino/robinhood-observatory/actions/runs/38974982322). Local: 149 passed, seven explicit PostgreSQL skips. The quote/session release also passed [154 tests](https://github.com/michaelbellino/robinhood-observatory/actions/runs/38973974274). Earlier evidence files are historical results, not the latest total.

Quote tests cover independent commits, missing/malformed value preservation, stale source times despite recent receipt, pause during reads, durable throttling, protected quote endpoints, batch rotation, session reuse/404 recovery and leadership checks after pacing. CI also runs migrations, source scanning and the locked dependency audit.

## Measurement rules

Measure broker HTTP duration, pacing, whole-cycle duration, quote completion gaps, source age and local receipt age separately. The shared application cap defaults to six MCP requests/minute; it is not a verified broker quota. The quote target is per batch, not a streaming or latency guarantee. More batches, pagination, outages and backoff extend cadence. See MARKET-DATA-2026-10-16.md.

Retain full-session evidence only after the complete reference exchange session. The observer records account and per-instrument quote sampling gaps, missing/stale quote samples and maximum observed quote age. Both account and quote criteria must pass. Five upcoming reference sessions can be scheduled idempotently; options-specific hours remain separate. Keep every fixture explicitly synthetic and separate from live data. Store operational evidence privately instead of committing account/deployment metadata to public source.
