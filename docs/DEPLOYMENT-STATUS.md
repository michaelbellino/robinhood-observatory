# Deployment verification guide — October 16, 2026

Deployment-specific account metadata, project/service identifiers, live measurements, private URLs and approval history belong in the owner's private operating record. This public file describes how to verify a deployment, not an individual's current environment.

The latest application change, `c902a4c31d40a6b4798428a6581eb1dec410a9ba`, passed [156 CI tests without skips](https://github.com/michaelbellino/robinhood-observatory/actions/runs/38974982322). The quote collector/reusable-session implementation is `4a6a898ca5f56384a5c36ac7506f71d1e34abe01`. Source-age presentation can be deployed to web without restarting an otherwise compatible worker.

For a Railway deployment, verify and retain privately:

1. Isolated web, persistent worker and PostgreSQL services; database/worker have no public networking. Sleeping disabled, supported restart policy, one replica and database leadership.
2. Explicit owner authentication, distinct read/control secrets, rejected anonymous and read-to-control requests. Never publish credentials or private financial fixtures.
3. Genuine independent brokerage enrollment and explicitly selected eligible account. A nickname, registration response or another connector's access is insufficient.
4. Successful reconciliation, separate quote progress/source timestamps, and persisted pause/checkpoint behavior after restart. Model access is optional; default analysis budgets are zero.
5. Actual token renewal/revocation behavior, external stopped/stalled-worker detection, notification receipt/deduplication and a restored production backup in isolation.
6. A completed service-owned full-session observation. Report scheduled/pending until the session elapses; a smoke test is not full-day proof.

Pin each deployed service to an exact tested commit; ordinary pushes must not deploy. Record deployed SHAs, migration level and rollback targets privately. Do not change shared subscriptions or another project's credentials/infrastructure. Obtain a concrete cost approval before paid provisioning; resource caps are not a total-bill cap.

See DEPLOYMENT.md, RECOVERY.md, COSTS.md and CHATGPT.md for configuration procedures. Keep direct authenticated MCP endpoint tests separate from successful enrollment and use through the intended ChatGPT workspace.
