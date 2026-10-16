# Capability matrix — October 16, 2026

This public matrix describes implementation and public protocol verification. Customer account metadata, deployment identifiers and live acceptance results belong in private owner-controlled evidence. A synthetic test or advertised capability is not proof of brokerage execution support.

| Capability | Status | Evidence / limitation |
|---|---|---|
| Official Trading MCP endpoint/auth metadata | Public metadata inspected | Discovery and schema validation implemented |
| Dynamic client registration | Implemented and protocol-tested | Echoed redirects do not prove consent acceptance |
| Hosted HTTPS consent | Deployment-specific / unverified generically | Use official supported authentication; retain actual failures privately |
| Native loopback enrollment | Implemented and tested | Real loopback, PKCE, owner-approved encrypted deposit; never import another app's session |
| Token refresh and recovery | Implemented and tested | Original client binding persists; actual lifetime/rotation/revocation require private live evidence |
| Read-only OAuth scope | Not advertised in inspected metadata | Runtime strict read allowlist is the enforcement boundary |
| Explicit account binding | Implemented and tested | Caller-relative eligibility rechecked; nicknames do not authorize access; no silent aggregation |
| Equities portfolio/positions/orders | Implemented and schema-tested | Missing values remain unknown; deployment-specific coverage must be verified |
| Equity/options quote polling | Implemented and tested | Separate durable cache, batching, source/receipt timestamps and freshness states |
| Options contracts, Greeks and liquidity | Implemented and schema-tested | Preserve unknown attributes; never assume a 100-share deliverable or common hours |
| Single/multi-leg options execution | Advertised in inspected connector metadata; unverified | No order previews or trades are part of runtime verification; approval level is insufficient proof |
| Custom or stock-option combo execution | Unsupported in this implementation | Structures remain explicitly unsupported |
| Broker mutation/preview tools | Inaccessible in hosted runtime | No generic broker proxy; strict method/tool/account allowlists |
| Broker alerts | Restricted | Reject user-wide alert tools without explicit account-scoped schema |
| Other/grouped asset/order coverage | Partial / unknown | Never infer zero exposure or complete orders from missing endpoints |
| Streaming/tick latency | Not enabled or verified for Robinhood | HTTP/SSE transport is not a verified quote subscription; no latency guarantee |
| Broker quota | Unverified | Shared six-request/minute application cap is conservative policy, not broker permission |
| Exchange calendars | Tested | Holidays, early closes and DST; unknown contract hours remain explicit |
| Single active scheduler | Real PostgreSQL tests passed | Persistent advisory leadership, crash recovery and post-pacing checks |
| Pause/checkpoints/quote cache | Persistence tests passed | Generation checks reject obsolete in-flight work; actual restart evidence stays private |
| API/dashboard/MCP owner authorization | Tests passed | Read/control separation, CSRF/Origin checks and fail-closed behavior |
| Model adapter and limits | Fixture-tested | Replaceable bounded adapter; zero default spend/calls; collection independent of AI |
| Paper proposals | Implemented | Separate, explicitly hypothetical and unfilled; no midpoint-touch fill claim |
| External outage detection | Sender implemented; deployment enrollment required | Success-only heartbeat; actual DOWN/UP and delivery receipt must be verified externally |
| Backups/restoration | Real isolated PostgreSQL fixture restore passed | Production scheduling and isolated production restore are separate gates |
| Railway portability | Containers/migrations/configuration implemented | Private worker/database; intentional pinned releases; actual service state is private |
| ChatGPT app connection | Authenticated MCP endpoints implemented | Secure tunnel or supported authenticated connection requires workspace-specific enrollment |
| Native-connector skill | Packaged separately | Does not bridge hosted credentials or hide another connector's tools |
| Full-session observation | Implemented | Durable next reference session; report pending until complete, then inspect evidence |
| Canvas design tooling | Unavailable in implementation session | Repository holds UI source; no simulated screenshot is live evidence |

## Primary sources

- [Robinhood onboarding](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
- [Robinhood agent tools](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
- [Protected-resource metadata](https://agent.robinhood.com/.well-known/oauth-protected-resource/mcp/trading)
- [Authorization-server metadata](https://agent.robinhood.com/.well-known/oauth-authorization-server/mcp/trading)
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) and [transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [GitHub OAuth](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
- [Railway backups](https://docs.railway.com/volumes/backups) and [pricing](https://docs.railway.com/pricing/plans)
- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

## Proprietary dependencies

Application code is MIT and container-portable. Brokerage accounts, authentication, quotes and data-use rights remain proprietary; access is not a redistribution license. Railway, GitHub identity/hosting and optional OpenAI services are external proprietary dependencies. Core monitoring does not require model access. The optional external heartbeat provider remains separate from application hosting. No Cloudflare or purchased market-data dependency is required. See MARKET-DATA-2026-10-16.md for free/open-source and commercial feed distinctions.
