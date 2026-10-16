# ChatGPT / Codex Work integration

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

No integration can wake an inactive chat merely because an event occurs. The external verified notification channel, persistent event history and scheduled observation are separate mechanisms. Model summaries run inside the service, independent of a chat being open.
