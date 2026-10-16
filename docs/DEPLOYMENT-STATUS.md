# Deployment status — 2026-10-16, 16:57 UTC

- Public MIT repository: https://github.com/michaelbellino/robinhood-observatory
- Dashboard: https://web-production-0eaab.up.railway.app/
- Isolated Railway project: `a7712f84-22c7-449a-995e-11c7f985685e`; production environment `378f95b4-5e05-46c3-a2ac-9d2a53d0eec0`.
- Web, persistent worker and private PostgreSQL are deployed successfully. Database and worker have no public domains or TCP proxies. Sleeping is disabled; restart policy is ALWAYS; one worker replica, PostgreSQL advisory leadership.
- Runtime source is pinned to `0b82049a221707987405af48c4b22b448c8994c8`. Ordinary pushes do not deploy. Later documentation commits do not change running code.
- CI: **94 passed, no skips**, including real PostgreSQL concurrency/crash recovery, persisted pause/checkpoints, migrations and encrypted isolated backup restoration.
- Hosted checks: anonymous API/read-MCP/control-MCP return 401; authenticated stored status returns 200; read credentials receive 403 on controls; repeated pause request is idempotent. Paused state survives worker redeployment. MCP initialization/tool listing succeeds with only monitor_status and monitor_history on the read endpoint.
- Dedicated GitHub OAuth app registered with exact callback, no wildcard/device flow; secret transferred directly into this service's Railway variables without appearing in prompts, screenshots, source or logs. Owner subject is explicitly bound. Deployed /auth/login returns 302 to GitHub HTTPS, read:user, PKCE S256. End-to-end owner login is pending: the cloud browser's URL policy blocked its navigation and no workaround was attempted.
- The hosted app independently registered its exact Robinhood callback successfully. Broker consent, live reads, binding, actual token lifetime/refresh/restart/revocation remain **unverified**. Monitoring is paused and unbound, with no successful live synchronization.
- Model spending and call limits remain zero. No model credentials, brokerage writes, previews, transfers, or real orders were used.

## Remaining owner steps and access gates

1. Open the dashboard in your browser, choose **Sign in**, then **Connect Robinhood** and complete the broker's own sign-in/consent. Choose **Verify account** and explicitly select the intended eligible account by masked number/type. Leave monitoring paused and report completion or the non-secret error. Do not send tokens/codes/screenshots containing credentials.
2. Native Railway backup creation and scheduling require **Pro**, according to this database's Backups screen. Current workspace is Hobby. No subscription change was made. Pro is $20/month versus Hobby $5/month, with corresponding included usage; actual incremental invoice depends on workspace usage. Decide on native Pro backups or a separately reviewed automated logical-backup destination before production reliability acceptance.
3. External missed-heartbeat check/notification channel and OpenAI Secure MCP Tunnel still need their own account/organization enrollment. No existing application's credentials are reused. The public authenticated MCP server works, but no ChatGPT connection has yet been installed or exercised.

Full-trading-session observation is pending and has not started. First successful live collection schedules the next complete reference session. A short hosted smoke test is not full-day reliability evidence.

The approved first-30-day incremental Railway envelope is $30, not an enforced total-bill cap. Only this project's resource limits were changed. Shared subscription/usage limits, Monarch, AdWorks and their credentials remain untouched. See COSTS.md for resource samples and exact caps.
