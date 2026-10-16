# ChatGPT / Codex Work integration

## Native ChatGPT workflow (2026-10-16)

The existing Robinhood connection in ChatGPT is usable independently of the failing Railway OAuth flow. Live portfolio/buying-power, equity/options position and order reads succeeded. A timestamped SPY quote succeeded. An explicitly authorized watchlist add/read/remove/read test restored the original empty state; no trades or transfers were attempted. Evidence is in `evidence/native-connector-2026-10-16.json`, without private identifiers or financial amounts.

`plugins/stock-observatory` provides a portable, MIT, skill-only observation workflow. Its private version 0.1.0 was created and the observe-brokerage skill appeared in the active skill catalog; ingestion readback matches the skill source. Package it with `python scripts/package_plugin.py`. It uses already connected Robinhood tools and optional authenticated Observatory tools. It contains no credentials, private account binding, server or app dependency with an invented ID. A plugin search did not return a canonical Robinhood app ID despite its active callable tools, so no guessed app reference was packaged.

Use it in the existing private chat, where account selection can be verified against an immutable ID. A new chat/task without that binding must request selection. Instructions constrain behavior but cannot make the native Robinhood mutation tools inaccessible. The hosted application retains the stronger programmatic read allowlist. Native observations do not write the application's database or prove hosted reconciliation.

The available Automations API explicitly limits recurrence to hourly. Do not create a two-minute task through it, silently substitute hourly, or stagger tasks to evade the limit. [Current official documentation](https://learn.chatgpt.com/docs/automations) separately describes minute-based schedules inside an existing chat. On October 16 the owner completed browser sign-in, and the signed-in scheduler was inspected: **Interval unit offers only hours; Custom repeat frequencies start at Hourly**. The active implementation chat's menus exposed no separate minute scheduler. Thus a two-minute schedule is unavailable in the account's inspected UI, despite the documentation. The unsaved form was canceled; no background task was enabled. Subsequent-run connector access and native task usage limits remain unverified. See `evidence/chatgpt-scheduler-2026-10-16.json`. The application's OpenAI API caps do not govern native ChatGPT plan usage.

Preferred final operation remains deterministic collection in the persistent worker, with bounded agent analysis on material changes or summaries. [MCP Events](https://developers.openai.com/plugins/build/mcp-events) support background Work reactions through authenticated subscriptions and verified webhook delivery; this is a real integration to implement, not an automatic consequence of storing an event. The currently exposed event sources do not include Robinhood or Railway. No substitute email/GitHub timer or Cloudflare dependency was created.

## Implemented application surface

| Authenticated endpoint | Tools | Credential |
|---|---|---|
| `/mcp/read/` | `monitor_status`, `monitor_history` (events, analysis, paper, work, observations) | Read token or owner session |
| `/mcp/control/` | `set_monitoring_paused(paused, request_id)` | Separate control token or owner session |

Use high-entropy, distinct bearer tokens and store only their SHA-256 hashes as web variables. Neither connection receives Robinhood tokens or any arbitrary brokerage tool. Every pause/resume is audited with actor and idempotency key. Read credentials fail on controls. Authentication, CSRF and DNS-rebinding checks are exercised locally. A hosted tool call is still pending.

## Preferred private transport

Current OpenAI documentation supports Secure MCP Tunnel from a customer-run client over outbound HTTPS. The target can be a privately reachable HTTP MCP service. Provision only after confirming the intended Platform organization and ChatGPT workspace association. Tunnel creation needs Read+Manage; the restricted runtime key needs Read+Use. These are separate from permission to add a ChatGPT custom MCP app.

Exact official setup: [Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels), [OpenAI guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [supported client onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md).

Prepared read target: `http://web.railway.internal:8000/mcp/read/`. If controls are enabled, use a separate connection/target and control credential; don't silently grant control to the read connection. Set `MCP_INTERNAL_HOST=web.railway.internal:8000` and verify the forwarded Host with the SDK's protection enabled. Never disable authentication for a tunnel.

The supported client download is selected by the Platform UI; this environment has no tunnel ID, runtime key or verified organization association. Therefore **no sidecar binary/version is guessed and no live plugin is installed**. Once access exists, use the official client help, pin the selected binary checksum, configure the private target via environment references, run `doctor`, verify readiness, then make actual ChatGPT status/history/control calls. Keep it as a Railway supervised service with no public admin UI. Add no generic HTTP proxy or Harpoon callout surface.

A public HTTPS endpoint with maintained bearer authentication is the fallback if supported by the selected ChatGPT connection interface. The application already supports it, but a hosted authenticated connection has not been tested. If the client requires OAuth rather than bearer auth, integrate a maintained authorization server with separate scopes instead of pretending the dashboard's OAuth login is an MCP authorization server.

An ordinary stored event does not wake an inactive chat. A supported, implemented and subscribed MCP Events connection can trigger Work; that capability is not configured here. External missed-heartbeat checks remain separate and must operate even if Railway is unavailable. Hosted model summaries can run independently of an open chat once optional AI is configured.
