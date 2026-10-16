# Durable implementation handoff — 2026-10-16

## Current state

This repository implements the application, migrations, containers, worker, dashboard, auth boundaries, MCP tools, broker OAuth/read allowlist, bounded AI adapter, options/paper data models, failure handling, restore tooling and manual release/CI workflows. See acceptance and capability documents for the exact distinction between local verification, connector evidence and live deployment.

An isolated Railway project named `robinhood-observatory` and empty `web`/`worker` service definitions were created. Restart ALWAYS, sleeping disabled and commands/Dockerfile path were configured for a future deployment. No database, volume, running deployment, public domain, credentials or paid usage was created. Existing applications were left alone. An initial public-domain action was rejected by automatic approval review because the service was not yet authenticated/deployed; no workaround was attempted.

A local git repository is prepared for `michaelbellino/robinhood-observatory`. The connected GitHub capability cannot create repositories, and the browser is signed out. No GitHub remote repository or hosted dashboard exists yet. Finish secure GitHub sign-in, create the empty public MIT repository, publish this reviewed source, and run CI. Do not upload `.env`, local financial state, tokens, dumps or private logs.

Existing Robinhood connector reads were reverified, but no credential was copied or extracted. The app needs its own supported OAuth consent. Official metadata supports refreshable PKCE clients and localhost registration worked; custom Railway platform support and account reuse remain unconfirmed. Do not open/fund another account or use unofficial login automation. Prepared support inquiry and exact callback fields are in SETUP.md.

## Next authorized implementation steps after unavoidable access/approval

1. Finish secure GitHub authentication and publish source; run the PostgreSQL CI gates. Visual review the authenticated UI in a reachable environment.
2. Obtain the concrete first-month incremental Railway approval in COSTS.md. The brokerage test allowance does not imply a recurring hosting budget.
3. Provision only this project's private DB/volume, configure owner OAuth/secrets, deploy web/worker, and verify public endpoint rejection before exposure/connection.
4. Confirm supported custom hosted Robinhood registration/consent, reuse of the intended existing agent account, quotas and refresh behavior. Owner completes broker consent and explicit account selection. Leave monitoring paused if blocked.
5. Configure daily backups and demonstrate an isolated encrypted export/restore. Verify real leadership/restart/database-failure behavior.
6. Configure the external dead-man channel, have owner verify receipt, and retain a deliberate worker-stop/stall DOWN/UP result.
7. Verify OpenAI Platform tunnel/workspace access, pin the supported client, deploy the private sidecar only if necessary, then install/test separate stored-read and monitor-control connections. AI can remain disabled with no model key.
8. Resume live collection and let the application schedule/record the next full reference session. Report pending until elapsed. Review actual request rate, failures, resources and cost before any reliability claim.
9. Develop investment methodology as a separate task using METHODOLOGY-HANDOFF.md. No real trading in this phase, even for a small test.

All independent work is retained in the source package. Remaining gates need credentials, broker/platform confirmation or paid hosting consent; they are not reported as completed.
