# Notifications and manual trade review — updated October 29, 2026

## Current execution boundary

The former dashboard submission design is disabled. `/api/trades/{id}/submit` and the submission implementation return HTTP 410. `OwnerOrderBroker` has no placement permission. There is no supported app approval that executes a brokerage transaction.

The current flow is sourced proposal → notification → owner sign-in and review → fresh broker preview → copy exact terms → owner enters and places the order in Robinhood → read-only observation of the exact broker order ID. The Robinhood link opens its homepage; it is **not** a prefilled ticket or native broker approval. Do not invoke `place_*` tools as draft creation.

Stock and option fields capture exact intended orders; this does not establish broker/account/session eligibility. Preparing the manual handoff requires fresh bid/ask evidence even for limit orders. The broker retains final checks. Read-only matching verifies the original account, immutable proposal version, exact instrument/legs/quantity/price/session/duration, user placement and timing. Acknowledgment is not a fill. Missing/stale evidence stays unknown; no similarity-based linking or automatic retries are allowed.

See [manual workflow and acceptance checklist](MANUAL-WORKFLOW-2026-10-29.md). Earlier deployment and device-receipt facts below are historical, not proof of current end-to-end readiness.

## Chosen delivery design

Use standards-based encrypted Web Push directly from the existing Railway web service. No notification SaaS account, Cloudflare service, SMS billing, or LLM call is required to deliver a message. Infrastructure usage may still change. The monitor runs in the existing worker; the web service drains the notification outbox independently every ten seconds, so slow brokerage collection does not block dispatch. This is an interval target, not a latency guarantee.

On iPhone: open `/trades` in Safari, add it to Home Screen, open that installed web app, sign in, then tap **Enable on this device**. Send the built-in test and verify receipt on the physical phone. This final device-permission/receipt step cannot be completed by the cloud browser.

Notifications intentionally omit financial details from the lock screen. They open the authenticated inbox and cannot execute or approve a transaction. The service worker never caches account pages or API responses. Session login, CSRF/Origin, strict owner checks and immutable hashes protect changes. Subscriptions and signing keys are encrypted in the existing database vault.

Delivery records distinguish **accepted_by_push_service** from phone receipt. Bounded retries use stable notification tags; delivery is at-least-once, not exactly-once. Permanent invalid subscriptions are disabled. Old/expired proposals are not sent. A stale notification already queued at the OS is labeled expired when displayed. DNS addresses are validated and pinned during delivery, TLS hostname verification is preserved, redirects are not followed, response sizes/timeouts are bounded, and only known browser push providers are accepted.

Web Push is not an outage detector when Railway is down. Existing Railway operational notifications and any separate external heartbeat remain different concerns.

## Duke / ChatGPT

Official OpenAI documentation supports MCP Events in Work Cloud chats and dots. The efficient architecture is Railway collection/filtering → a signed subscribed event → Duke reasoning/notification. A dot need not repeatedly poll every quote. This is a supported product mechanism, not an arbitrary incoming-message API.

The installed Stock Observatory plugin currently contains a skill, not an authenticated connection to this hosted application's MCP endpoints. At inspection, Observatory was absent from connected automation event sources. Therefore no ChatGPT event subscription was created, no callback was invented, and no hourly automation was substituted for timely alerts.

To add ChatGPT delivery later: enroll the hosted MCP endpoint in the intended workspace, implement the MCP 2.0 event methods and persistent verified callbacks, rescan the plugin, create the event subscription, then test challenge verification, receipt, refresh/unsubscribe, and an actual resulting ChatGPT notification. Use the existing Secure MCP Tunnel when its endpoint/authentication binding is available; no Cloudflare dependency is required. The current hosted read tool `trade_review_inbox` and control tool `propose_trade_for_review` are ready for an authenticated connection, but installing a skill alone does not connect them.

## Alternatives researched

| Route | Assessment |
| --- | --- |
| Direct Web Push | Chosen immediate path; existing host, no per-message provider subscription; requires phone enrollment and receipt test. |
| ChatGPT MCP Events | Best fit for Duke narration; requires real event-server implementation, plugin connection and subscription verification. Not active in this release. |
| Robinhood Trade approvals | Potentially simpler final order review inside Robinhood; official help says rollout is incomplete for external MCP accounts. Availability not verified on this account. Do not call a placement tool on the assumption it only creates a draft. |
| SMS/email/third-party push relay | Adds accounts, billing or delivery dependencies; unnecessary for this first channel. |
| Repeated ChatGPT polling | Adds redundant reasoning and is not a market-timing transport. Not configured. |

Robinhood's public help and connected tool metadata differ on multi-leg support. Tool metadata advertises spreads for eligible accounts, while public help states built-in agents cannot place multi-leg orders. Treat account/client/structure-specific support as requiring actual validation; do not generalize either statement into a verified live execution capability.

## Remaining gates

- Phone Web Push receipt was confirmed by the owner on October 20, 2026; that is separate from trade execution.
- Define the trading methodology and mandate before autonomous proposal generation.
- Configure a separately approved API model/budget if hosted model reasoning is wanted. Existing API spend remains disabled.
- Broker preview calls have mock transport coverage; verify an owner-selected live proposal without submitting an order.
- Final order placement takes place only in Robinhood. No real money is used for implementation tests. Hosted sign-in, current device receipt, and read-only observation must be verified before calling the full workflow proven.

## Primary sources

- [OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events)
- [Dots tasks and event monitoring](https://learn.chatgpt.com/docs/dots/tasks-and-memory)
- [WebKit: iPhone Home Screen Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)
- [pywebpush source](https://github.com/web-push-libs/pywebpush)
- [Robinhood trade approvals](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
