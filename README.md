# Robinhood Observatory

Portable, single-owner account monitoring and bounded research. **Observe only.** No brokerage write, preview, cancel, exercise, transfer or permission-changing tools are exposed or implemented. Paper proposals are separate, hypothetical, unfilled records.

**2026-10-16 implementation status:** independent account reconciliation and quote polling, reusable MCP sessions, durable checkpoints, authenticated dashboard/MCP, and observe-only analysis infrastructure are implemented. The latest release passes 181 automated tests including real PostgreSQL recovery/restore tests. Deployment-specific account metadata, operational measurements and acceptance evidence are retained privately by the owner. See [market-data research](docs/MARKET-DATA-2026-10-16.md), [capabilities](docs/CAPABILITIES-2026-10-16.md) and [handoff](docs/HANDOFF.md). Test results do not establish a deployment's full-session reliability.

## Components

- **Paper workbench at `/workbench`:** a no-model command assistant for stored status/quotes and isolated stock/option review rehearsals. Immutable terms, five-minute expiry, duplicate protection and database-enforced paper caps. No real-money execution. See [workbench testing](docs/PAPER-WORKBENCH.md).
- FastAPI + responsive static dashboard; GitHub OAuth (OIDC alternative), explicit immutable single-owner subject, secure session/CSRF controls. Separate bearer credentials for MCP reads and monitoring controls.
- Persistent Python worker; one PostgreSQL advisory-lock leader, durable work records, checkpoints, pause state, idempotent events and shared request pacing. No Redis.
- PostgreSQL application state with Alembic migrations. Encrypted broker credentials, including refresh tokens, survive restarts. Secrets and financial data stay out of the repository.
- Options contracts, legs, strategies, source timestamps and explicit unknowns. Quote polling, not streaming. Observe-only methodology interface.
- Replaceable OpenAI Responses adapter; structured validation, source references, no tools, minimized input, daily/monthly application spending reservations. AI defaults off and never gates core collection.
- Authenticated stored-data MCP at `/mcp/read/` and `/mcp/control/`. The latter only pauses/resumes monitoring. Neither is a brokerage proxy.

## Local development

Requires Docker Compose, or Python 3.12 + PostgreSQL 17. Dependencies are pinned in `uv.lock`; container images are pinned by digest. The `.railway/railway.ts` declaration uses a locked TypeScript SDK and is deliberately not auto-applied.

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check observatory tests migrations scripts
npm ci --ignore-scripts
npm run check:infra
```

Without `TEST_DATABASE_URL`, seven PostgreSQL tests explicitly skip. That is **not** an acceptance pass. Dedicated test databases must contain `test` in their names; the test fixture resets only that database. Run against a disposable database, never production. CI supplies one and exercises encrypted backup/restore.

For a complete local stack:

1. Run `uv run python -m scripts.init_local` to create ignored `.env` from the example and generate local-only secrets without printing them. Set `OWNER_SUBJECT` to the intended provider's immutable user ID, and complete the OAuth fields per [setup](docs/SETUP.md).
2. `docker compose up --build -d`. Migrations run before web/worker. The worker starts paused and unbound; it cannot read a brokerage account until independently authorized and explicitly selected.
3. Open `http://127.0.0.1:8000`. No fixture data appears in the dashboard. If owner OAuth is not configured, sign-in returns a clear setup error; no anonymous data or control access is allowed.
4. For shutdown, `docker compose down` keeps the durable database volume. Do not add `--volumes` unless intentionally deleting disposable local state.

## Deployment and operations

Read [setup](docs/SETUP.md), [deployment/rollback](docs/DEPLOYMENT.md), [recovery/rotation](docs/RECOVERY.md), [ChatGPT integration](docs/CHATGPT.md), [costs](docs/COSTS.md), and [acceptance evidence](docs/ACCEPTANCE.md). Railway is the sole application host. A separate external dead-man heartbeat service detects missed work outside Railway. No Cloudflare dependency.

Automatic production deployment from ordinary pushes is disabled by design. The release workflow is manual, takes an exact reviewed commit, and requires the `railway-production` protected environment. A database migration/restore and live-auth gate must pass before the first release is promoted.

MIT license covers this application, not Robinhood brokerage/market-data rights, Railway service terms or OpenAI service access. It is not affiliated with Robinhood or OpenAI. Proprietary external dependencies and licensing limits are listed in [capabilities](docs/CAPABILITIES-2026-10-16.md).

## Trade review and phone notifications

`/trades` provides a real-instrument research inbox, broker previews, encrypted phone Web Push, and matching observed-order tracking after **manual submission in Robinhood**. Automatic trade selection, one-click brokerage submission and ChatGPT event delivery are not enabled. See [workflow, setup, limitations and sources](docs/NOTIFICATIONS-AND-TRADE-REVIEW.md). The synthetic workbench remains separate.
