# Deliberate Railway deployment and rollback

Use only the isolated `robinhood-observatory` project. Never change Monarch, AdWorks, shared credentials or workspace billing. An existing subscription is not authorization for incremental spending. Obtain the concrete approval in [COSTS.md](COSTS.md) before applying infrastructure or running compute.

## Desired state

`.railway/railway.ts` is typechecked against pinned Railway SDK 3.16.0. New projects use Railway Infrastructure as Code; legacy railway.json/toml Config as Code is deprecated. Do not connect the repository as an automatic deployment source.

| Service | Configuration |
|---|---|
| web | Same portable Dockerfile; Uvicorn factory; fixed internal port 8000; `/health/live`; predeploy `alembic upgrade head`; ALWAYS restart; sleeping disabled; one replica |
| worker | Same image; `python -m observatory.worker`; ALWAYS restart; sleeping disabled; one replica; no public networking |
| database | PostgreSQL 17.7 digest pinned; persistent volume `/var/lib/postgresql/data`; no public domain or TCP proxy |
| optional tunnel | Only after OpenAI tunnel enrollment; private target `http://web.railway.internal:8000/mcp/read/`; no public ingress; separately pinned supported client |

Preserved secret references must be populated before deployment; they are not placeholder credentials. Web needs owner OAuth, MCP token hashes, encryption/session keys. Worker needs database/encryption, timing, optional heartbeat/notification and model secrets. Keep the OpenAI model key out of web. The broker token vault is shared through PostgreSQL. Use the private database reference, never `DATABASE_PUBLIC_URL`.

## First release sequence

1. Create the GitHub repository under the intended owner and publish only tracked reviewed source. Enable branch protection/required Verify checks and a `railway-production` environment with owner approval. Do not attach a push-triggered Railway source.
2. Run CI with a disposable PostgreSQL 17 service. Require all PostgreSQL gates, the source scan, locked-dependency audit and IaC typecheck to pass. Build the pinned Dockerfile and record image digest.
3. After hosting approval, plan infrastructure against the isolated project and inspect the complete diff: `railway config plan --out /tmp/observatory-plan.json`. Confirm no unrelated resource and no public database/worker proxy. Apply only that reviewed plan using the current supported CLI. CLI auth must be dedicated, never copied from this chat's connector.
4. Provision the new database/volume; configure owner authentication and secrets; deploy/migrate web while the monitor remains paused. Public domain generation was blocked by automatic approval review before an authenticated deployment existed. Retry only once this prerequisite is satisfied; do not bypass the block.
5. Create the authenticated web HTTPS origin. Set exact `PUBLIC_URL`, OAuth callbacks and MCP allowed host. Verify anonymous status/MCP →401, read credential control→403, wrong owner→403, correct owner session, and CSRF rejection. Test direct origin and tunnel host separately.
6. Deploy the worker with sleeping disabled and ALWAYS restart. Confirm effective live config, one physical leader and persisted worker progress. The advisory lock permits deploy overlap without two logical active schedulers. Worker/database remain private.
7. Complete the independent Robinhood gate. Without it, leave production paused and unbound; do not import fixture snapshots or claim live operation.
8. Configure daily Railway volume backups, plus encrypted logical exports. Restore one export into an isolated database using the recovery procedure before declaring recoverability.
9. Configure an external dead-man check, verify its delivery channel with the owner, and perform a deliberate stopped/stalled-worker test. Resume, reconcile and verify recovery before starting observation.
10. First successful live worker cycle automatically schedules a **next complete XNYS reference session** observation. Persisted `session_observations` and `work_records` retain coverage/gaps/failures. Inspect the result after the session elapses; it does not prove options-hour coverage or future reliability. `/api/observation` can schedule another run. No chat-wake assumption.

## Routine release

`.github/workflows/release.yml` is `workflow_dispatch` only. Supply the exact 40-character SHA that passed Verify and acknowledge the release window. Store a project-scoped Railway token in the protected environment, not source. The workflow deploys web/migration then worker and serializes production releases. Review whether to pause briefly outside the relevant instrument's session; pause/resume is audited. Reconciliation must finish before analysis resumes.

The workflow requires a protected environment and operator review of Verify. GitHub branch protection/environment approval is not configured yet. The workflow alone does not establish these policy gates.

## Rollback

Pause via the control scope; note checkpoint and current release SHA. Deploy the prior immutable tested SHA/image to web and worker. This phase's migrations are additive; do not automatically downgrade a production database. If schema incompatibility exists, stop and restore into an isolated environment, validate hashes and explicit pause state, then switch only after review. Never restore over an active production database. Resume only after owner authentication, broker authorization and full reconciliation pass. Preserve failed release/work records for investigation.

## Operational measurement

Work records capture request count, throttles, request elapsed milliseconds, cycle duration and maximum RSS. Railway CPU/RAM/volume/egress metrics must be measured after deployment. No production resource measurement exists yet. Monitor progress via authenticated `/api/status` and `/api/history/work`; `/health/live` reports only the web process.
