# Notifications and trade review — October 20, 2026

## Delivered scope

`/trades` is an authenticated research inbox for actual stock/option terms, separate from synthetic `/workbench` rehearsals. Producers supply exact terms, a methodology/version, rationale, risks, sources, and a bounded expiry. The hosted app does not yet generate discretionary recommendations: the trading mandate is deliberately a separate decision, and paid model budgets remain unchanged.

The pipeline is proposal → durable notification → owner review → explicit broker preview → manual order entry/submission in Robinhood → link to the matching observed order. Reviewing an idea never submits an order. This release does not implement one-click broker submission. It does not create a native Robinhood pending-approval ticket. There is no live-trading flag to switch on.

The separate `PreviewBroker` permits only the existing reads plus `review_equity_order` and `review_option_order`. Worker reads remain unchanged. No place, replace, cancel, exercise, transfer, or generic broker proxy is exposed. Exact preview echoes are checked; options identities and multipliers are verified against instruments. Broker disclosures/alerts remain visible. Source quote staleness and preview expiry prevent recording a proceed decision; the user must still recheck the live market when entering the order in Robinhood.

Supported review forms: whole-share equity limit orders, and one-to-four-leg options limit orders, regular hours, good for day. This is a narrower implementation than every broker-advertised order type. Unsupported forms fail validation. Proposed limits are not account risk caps. No strategy has been approved or evaluated by these workflow tests.

Order status comes only from the independently collected selected-account snapshot. The owner supplies an actual order UUID; the application checks asset, quantity, price, direction/legs, session, time in force, and creation time. A user claim or review decision never counts as a fill. Existing incomplete grouped-order warnings remain material; API availability is not proof of complete account coverage.

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

- Enroll the user's actual device and confirm a push arrived; provider acknowledgment alone is insufficient.
- Define the trading methodology and mandate before autonomous proposal generation.
- Configure a separately approved API model/budget if hosted model reasoning is wanted. Existing API spend remains disabled.
- Broker preview calls have mock transport coverage; verify an owner-selected live proposal without submitting an order.
- Actual live-order submission and subsequent fill tracking require the user to submit the order in Robinhood. No real money was used for implementation tests.

## Primary sources

- [OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events)
- [Dots tasks and event monitoring](https://learn.chatgpt.com/docs/dots/tasks-and-memory)
- [WebKit: iPhone Home Screen Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)
- [pywebpush source](https://github.com/web-push-libs/pywebpush)
- [Robinhood trade approvals](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
