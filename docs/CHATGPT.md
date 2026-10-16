# ChatGPT / Codex Work integration

## Native ChatGPT workflow

`plugins/stock-observatory` provides a portable MIT observation skill. Package it with `python scripts/package_plugin.py`. It can use already connected Robinhood tools and optional authenticated Observatory tools, but contains no credentials or private account binding. A skill is not a token bridge and cannot make another connector's mutation tools inaccessible. The hosted application's read allowlist is the stronger enforcement boundary.

Verify account binding in each intended chat/task; native observations do not write this application's database or establish its independent authorization. Keep native connector testing and deployment-specific access evidence private.

Scheduling availability varies by the supported interface. Follow its actual minimum recurrence; do not silently substitute a different cadence, stagger tasks, or use sleep loops to evade limits. Continuous deterministic collection belongs to the persistent worker. ChatGPT-native task usage is separate from this application's OpenAI API caps.

[MCP Events](https://developers.openai.com/plugins/build/mcp-events) can support background Work reactions through authenticated subscriptions and verified delivery. This requires an actual enrolled event integration; storing a database event alone does not wake a chat. No Cloudflare dependency is required.

## Implemented application surface

| Authenticated endpoint | Tools | Credential |
|---|---|---|
| `/mcp/read/` | `monitor_status`, `monitor_history` (events, analysis, paper, work, observations) | Read token or owner session |
| `/mcp/control/` | `set_monitoring_paused(paused, request_id)` | Separate control token or owner session |

Use high-entropy, distinct bearer tokens and store only their SHA-256 hashes as web variables. Neither connection receives Robinhood tokens or any arbitrary brokerage tool. Every pause/resume is audited with actor and idempotency key. Read credentials fail on controls. Authentication, CSRF and DNS-rebinding checks are exercised locally. Record direct endpoint tests separately from a successful ChatGPT-originated call through the enrolled application connection. Keep deployment-specific results private.

## Preferred private transport

Current OpenAI documentation supports Secure MCP Tunnel from a customer-run client over outbound HTTPS. The target can be a privately reachable HTTP MCP service. Provision only after confirming the intended Platform organization and ChatGPT workspace association. Tunnel creation needs Read+Manage; the restricted runtime key needs Read+Use. These are separate from permission to add a ChatGPT custom MCP app.

Exact official setup: [Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels), [OpenAI guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [supported client onboarding](https://github.com/openai/tunnel-client/blob/master/docs/onboarding.md).

Prepared read target: `http://web.railway.internal:8000/mcp/read/`. If controls are enabled, use a separate connection/target and control credential; don't silently grant control to the read connection. Set `MCP_INTERNAL_HOST=web.railway.internal:8000` and verify the forwarded Host with the SDK's protection enabled. Never disable authentication for a tunnel.

Select the supported client through the Platform UI after verifying organization/workspace access. Do not guess a tunnel ID, runtime key, client binary/version or app association. Once access exists, use the official client help, pin the selected binary checksum, configure the private target via environment references, run `doctor`, verify readiness, then make actual ChatGPT status/history/control calls. Keep it as a Railway supervised service with no public admin UI. Add no generic HTTP proxy or Harpoon callout surface.

A public HTTPS endpoint with maintained bearer authentication is the fallback if supported by the selected ChatGPT connection interface. Verify that connection against the actual client and retain the result privately. If the client requires OAuth rather than bearer auth, integrate a maintained authorization server with separate scopes instead of pretending the dashboard's OAuth login is an MCP authorization server.

An ordinary stored event does not wake an inactive chat. A supported, implemented and subscribed MCP Events connection can trigger Work; that capability requires explicit enrollment and verification. External missed-heartbeat checks remain separate and must operate even if Railway is unavailable. Hosted model summaries can run independently of an open chat once optional AI is configured.
