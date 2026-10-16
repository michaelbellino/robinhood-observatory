# Capability matrix — 2026-10-16 UTC

“Verified” refers to the stated environment only. A local synthetic test is not hosted brokerage evidence. No previews or real brokerage mutations were invoked.

| Capability | Status | Evidence / limit |
|---|---|---|
| Official Trading MCP endpoint and auth metadata | Verified, unauthenticated | Public metadata HTTP 200; evidence JSON retained |
| Dynamic client registration | Verified for localhost and deployed Railway callback | Hosted app registered exact HTTPS callback with PKCE S256; no authorization/token obtained |
| Custom Railway-hosted OAuth | Registration verified; consent unverified | Actual deployed app received registration success; owner consent/live reads still pending |
| PKCE, grants, client authentication | Metadata verified | S256, authorization_code + refresh_token, token endpoint auth none |
| Read-only OAuth scope | Unsupported in advertised metadata | Only internal advertised; application read allowlist is the enforcement boundary |
| Token lifetime, rotation, long-lived refresh, broker revocation | Live unverified | Failure/rotation simulations pass; no revocation endpoint advertised; local disconnect implemented |
| Existing ChatGPT connector account reads | Verified in that connector | Active selected account and caller access/type/options level rechecked; private results omitted from source |
| Hosted app account binding/read | Unverified | No independently authorized token; runtime rejects unselected/ineligible account |
| Equity portfolio/positions/orders/quotes | Connector reads and local schema handling verified | No claim of independently hosted live data; source/receipt timestamps distinct |
| Options instruments/quotes/Greeks/IV/liquidity | Connector sample reads verified | Contract multiplier and expiration returned; unavailable attributes remain null |
| Single-leg and multi-leg options | Advertised, not execution-verified | Current connector metadata; approval level is not a successful preview/trade |
| Stock-option combo, arbitrary custom structure execution | Unsupported | Marked unsupported; no broker write path exists |
| Broker preview/place/cancel/replace/exercise/transfer | Disabled / inaccessible | Allowlist rejects even discovered write/preview tools; no generic proxy |
| Options deliverable/exercise style/settlement details | Unknown where unavailable | No automatic 100-share deliverable or uniform-hours assumption |
| Broker alert events | Restricted / incomplete | User-wide alert schema rejected without explicit account binding |
| Crypto/grouped/other asset coverage | Partial / unknown | Read selected linked account if schema supports; total open-order coverage never falsely complete |
| Streaming/tick latency | Unsupported in this version | MCP request polling only; no guaranteed latency |
| Broker request quota | Unverified | No published quota located; conservative shared 6 requests/minute application budget |
| Exchange calendars | Local verified | Holiday, early close and DST tests; unknown options hours retain active polling |
| FastAPI/dashboard/MCP protection | Local and deployed boundary verified | Deployed anonymous 401, read→control 403, authenticated status and MCP initialization/tool list; owner browser login pending |
| Persistent pause/checkpoints/event dedupe | Real PostgreSQL fixture verified | Concurrent leadership, crash recovery, pause/checkpoint persistence and restore pass in CI |
| OpenAI summaries and hard app reservation limits | Local fixture verified | Actual API key/model absent; default spend/calls zero; monitoring works without analysis |
| External stopped-worker detection | Implemented sender, delivery unverified | Live external check/account/channel not configured |
| Backups and isolated restoration | Real PostgreSQL fixture restore verified; production blocked | Native Railway backup scheduling requires Pro on the current Hobby workspace; no subscription changed |
| Railway deployment | Deployed | Private PostgreSQL and worker; HTTPS web; immutable source; dedicated credentials; paused and unbound |
| ChatGPT Secure MCP Tunnel | Documented approach verified; connection unverified | Runtime key, tunnel/workspace association and supported binary enrollment pending |
| Full trading-session observation | Pending, not started | Auto-scheduled by first successful live cycle; no short test substituted |
| Canvas design surface | Unavailable in this session | No callable new-Canvas surface; responsive source UI implemented, deployed public shell inspected; authenticated/mobile visual review pending |
| Prior Webull materials | No reusable material readily located | No old methodology copied; search did not delay implementation |

## Primary sources inspected

- [Robinhood external-agent onboarding](https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/)
- [Robinhood trading with an agent](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
- [Protected-resource metadata](https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading)
- [Authorization-server metadata](https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading)
- [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [GitHub OAuth authorization](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
- [Railway IaC](https://docs.railway.com/infrastructure-as-code), [volume backups](https://docs.railway.com/volumes/backups), [pricing](https://docs.railway.com/pricing/plans)
- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

## Proprietary dependencies

The app, its protocol adapters and containers are portable MIT source. Robinhood accounts, authentication, brokerage services, quotes and usage/license terms remain proprietary. API data access is not a redistribution license. Railway managed hosting and OpenAI API/tunnel access are proprietary services; core monitoring does not depend on OpenAI availability. GitHub hosts source/owner identity. Healthchecks' hosted service is optional and externally operated; its open-source project may be substituted by an independently hosted compatible dead-man endpoint. No Cloudflare or purchased market-data dependency is present.
