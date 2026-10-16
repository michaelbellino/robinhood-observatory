# Implementation handoff — October 16, 2026

This public repository is the source of truth for portable application code, tests and generic operating procedures. Keep selected-account metadata, credentials, project/service identifiers, real operational logs, financial fixtures and deployment-specific acceptance evidence in private owner-controlled storage. Do not infer a deployment's live state from this document.

## Current implementation

The quote/session change is commit `4a6a898ca5f56384a5c36ac7506f71d1e34abe01`; source-age presentation is `c902a4c31d40a6b4798428a6581eb1dec410a9ba`. The latter passed [156 tests in CI](https://github.com/michaelbellino/robinhood-observatory/actions/runs/38974982322), including real isolated PostgreSQL tests. Local verification: 149 passed and seven explicit PostgreSQL skips. Do not represent local skips as passes.

One PostgreSQL advisory leadership connection owns serialized account and quote work. The worker reuses its MCP/HTTP client and tool schemas, with bounded read-only reinitialization after session expiry. Checks after pacing enforce leadership, pause/generation and credential health immediately before network I/O. A durable quote cache updates independently of full account snapshots; batch rotation shares the same request budget. Quote success does not advance account reconciliation or its external heartbeat.

The dashboard reads stored quote data every five seconds without increasing broker requests. It separates source age from receipt age and shows missing/stale source times explicitly. All data/control endpoints remain authenticated and owner-bound; read and control credentials are separate. MCP exposes only stored status/history and audited monitor controls, never an arbitrary brokerage proxy.

The runtime is observe-only. Model access defaults off with application call/spending limits at zero. Paper proposals are separate and unfilled. Brokerage previews, orders, cancellations, replacements, exercises, transfers, funding and permission changes are absent from the runtime allowlist.

## Deployment acceptance checklist

Record actual results privately for independent supported consent/account eligibility, token lifetime/rotation/revocation, restart recovery, single leadership, malformed/throttled/stale/auth/DB failures, unauthorized endpoint rejection, model-independent monitoring, external DOWN/UP receipt, production backup restoration and secret/mutation checks. The real PostgreSQL fixture restore in CI is not a restored production backup.

Use the service's durable next-session observation record and wait until the whole reference session has elapsed. Quote freshness, account continuity and options-specific hours require separate evaluation. A short after-hours test is insufficient to claim full-day reliability.

Enrolling the application's authenticated MCP endpoint in ChatGPT is separate from testing the endpoint directly or installing a skill that uses another existing brokerage connector. Do not copy connector credentials, guess tunnel credentials, or evade scheduler frequency limits with staggered tasks/sleep loops. Continuous collection belongs to the persistent Railway worker.

## Recovery and further work

Source pins make deployment deliberate. Release web/migration before worker, retain the prior tested SHA and leave additive migrations in place on rollback. Never restore over active production. Keep the database and worker private, disable worker sleeping, and verify a supported restart policy. See DEPLOYMENT.md, RECOVERY.md and COSTS.md.

Use NATIVE-CONNECTION.md for enrollment. When credentials are already saved, verify/select the account instead of reconnecting. A registration response alone is not proof of accepted consent. Do not open or fund a new brokerage account without a verified requirement and owner action.

See MARKET-DATA-2026-10-16.md for primary-source feed research. The default shared six-request/minute cap is conservative application policy, not a verified Robinhood quota. No streaming or per-instrument latency guarantee is asserted. Unknown instrument hours remain explicit.

The later methodology phase belongs in METHODOLOGY-HANDOFF.md. Preserve the versioned observe-only interface, source references, untrusted-content boundaries and hard analysis budget. No investment strategy is part of this implementation phase.
