# Durable implementation handoff — 2026-10-16

The application and deployment exist. Read DEPLOYMENT-STATUS.md first for the latest observed state; ACCEPTANCE.md and CAPABILITIES-2026-10-16.md distinguish synthetic, real PostgreSQL, hosted smoke, existing-connector and independent-broker evidence.

## Implemented and verified

FastAPI/dashboard, owner authorization, read/control MCP separation, official Robinhood OAuth with durable encrypted tokens, strict brokerage read allowlist, bounded polling, calendars/options models, observe-only versioned strategy interface, paper-only proposals, bounded OpenAI adapter, database leadership/checkpoints/audit/dedupe, failure handling and portable release/recovery tooling. CI has 115 passing tests including a real isolated PostgreSQL restore. Web, worker and private database are deployed from an immutable commit. Hosted unauthorized rejection, read-only authorization, MCP tool discovery, pause idempotence and persistence after worker restart are verified. Evidence files contain no private financial records or credentials.

## Resume from here

- Owner completes dashboard GitHub login and independent Robinhood consent/account selection in their browser; the cloud browser blocked the dashboard sign-in navigation. Registration already succeeded for the deployed HTTPS broker callback. Do not ask for support contact unless the actual supported consent/refresh path produces a blocker. Do not reuse this chat's broker session or credentials.
- Verify the selected account's caller-relative eligibility, type/options level and live reconciliation. Record actual token lifetime, rotation and recovery without retaining secret values in evidence. Keep unknown values and incomplete asset coverage explicit.
- Resolve the production backup gate. Native Railway backup scheduling requires Pro on the current Hobby workspace; no upgrade is authorized. CI restoration is verified but production restoration is not. A subscription change affects shared billing and requires a concrete owner decision.
- Enroll and test an external dead-man check and generic notification channel. Use a 35-minute period plus 5-minute grace for the prepared off-hours cadence, then verify actual DOWN/UP receipt during a deliberate worker stop/stall. Do not claim monitoring will wake a chat.
- Enroll a dedicated OpenAI Secure MCP Tunnel using the intended organization/workspace, supported client and restricted runtime key, or verify the chosen client's authenticated HTTPS support. Keep read and control credentials separate. No plugin is installed merely to test connectivity; no arbitrary broker proxy exists.
- Resume live monitoring only after authenticated account binding. The service schedules its own next full-session observation on first successful live sync. Retain results and review real polling/error/resource/cost evidence; report pending until the session elapses.
- AI remains disabled at a zero budget. Later methodology work belongs in METHODOLOGY-HANDOFF.md; no investment strategy or real trading is authorized in this phase.

The repository is the source of truth. Production source is pinned to a specific SHA; pushes do not auto-deploy. Release deliberately and retain rollback/credential rotation instructions in DEPLOYMENT.md and RECOVERY.md. Only isolated application resources may be modified.

## Active OAuth investigation

Owner GitHub login is verified. Robinhood Allow fails before any app callback; shared default client/redirect acceptance is a hypothesis awaiting the failed consent response detail. Read OAUTH-INVESTIGATION-2026-10-16.md before changing authentication. The new owner-only diagnostics and protocol regression tests address callback/error-reporting gaps without weakening OAuth. No live broker connection or full-session observation has been proven.

The OAuth diagnostic release `936a4303904dbc9df4cc43a5788cb090f8f5a2c8` is pinned on both services. Release evidence: `evidence/oauth-release-2026-10-16.json`. CI run 38941182829 passed all 115 tests, secret scan and dependency audit. The remaining owner step is to capture only the failed Robinhood consent request HTTP status and non-secret error detail in desktop browser Network tools; no HAR or session material.
