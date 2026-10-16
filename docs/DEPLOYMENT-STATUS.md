# Deployment status — 2026-10-16 (updated during consent investigation)

- Public MIT repository: https://github.com/michaelbellino/robinhood-observatory
- Dashboard: https://web-production-0eaab.up.railway.app/
- Isolated Railway project: `a7712f84-22c7-449a-995e-11c7f985685e`; production environment `378f95b4-5e05-46c3-a2ac-9d2a53d0eec0`.
- Web, persistent worker and private PostgreSQL are deployed successfully. Database and worker have no public domains or TCP proxies. Sleeping is disabled; restart policy is ALWAYS; one worker replica, PostgreSQL advisory leadership.
- Web and worker are pinned to OAuth diagnostic release `936a4303904dbc9df4cc43a5788cb090f8f5a2c8`; both deployments succeeded on 2026-10-16 at approximately 17:41 UTC. Ordinary pushes do not deploy. No database schema changes were needed.
- CI: **115 passed, no skips**, including real PostgreSQL concurrency/crash recovery, persisted pause/checkpoints, migrations and encrypted isolated backup restoration.
- Hosted checks: anonymous API/read-MCP/control-MCP return 401; authenticated stored status returns 200; read credentials receive 403 on controls; repeated pause request is idempotent. Paused state survives worker redeployment. MCP initialization/tool listing succeeds with only monitor_status and monitor_history on the read endpoint.
- Dedicated GitHub OAuth app registered with exact callback, no wildcard/device flow; secret transferred directly into this service's Railway variables without appearing in prompts, screenshots, source or logs. Owner subject is explicitly bound. Deployed /auth/login returns 302 to GitHub HTTPS, read:user, PKCE S256. The owner subsequently completed GitHub sign-in; production callback and authenticated API logs verify that login works.
- The hosted app received a Robinhood registration response echoing its callback, but consent fails after Allow. No broker callback reaches the app. This is not yet a working independent connection. See OAUTH-INVESTIGATION-2026-10-16.md for evidence and the remaining diagnostic. Live reads, binding and token behavior remain **unverified**. Monitoring is paused and unbound.
- Model spending and call limits remain zero. No model credentials, brokerage writes, previews, transfers, or real orders were used.

## Remaining owner steps and access gates

1. Inspect the failed Robinhood consent request's HTTP status and non-secret error detail in the owner's browser. No Railway callback has arrived. Do not repeatedly retry or assume client registration proves the redirect is accepted. After resolving consent, explicitly select and verify the intended account; do not resume merely on the basis of its nickname.
2. Native Railway backup creation and scheduling require **Pro**, according to this database's Backups screen. Current workspace is Hobby. No subscription change was made. Pro is $20/month versus Hobby $5/month, with corresponding included usage; actual incremental invoice depends on workspace usage. Decide on native Pro backups or a separately reviewed automated logical-backup destination before production reliability acceptance.
3. External missed-heartbeat check/notification channel and OpenAI Secure MCP Tunnel still need their own account/organization enrollment. No existing application's credentials are reused. The public authenticated MCP server works, but no ChatGPT connection has yet been installed or exercised.

Full-trading-session observation is pending and has not started. First successful live collection schedules the next complete reference session. A short hosted smoke test is not full-day reliability evidence.

The approved first-30-day incremental Railway envelope is $30, not an enforced total-bill cap. Only this project's resource limits were changed. Shared subscription/usage limits, Monarch, AdWorks and their credentials remain untouched. See COSTS.md for resource samples and exact caps.
