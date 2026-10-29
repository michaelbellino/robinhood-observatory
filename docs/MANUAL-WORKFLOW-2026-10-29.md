# Manual placement workflow and remaining acceptance gates

## What the code does

1. Keep sourced research/watch scenarios separate from an execution-ready proposal. Prior-close quotes may support clearly dated research, never a current executable price. Missing data or model configuration is explicit, not a silent successful run.
2. Save exact stock/option intent with immutable proposal version and bounded deadline. The review page includes rationale, sources, risk and exit evidence when supplied by the research path.
3. Owner sign-in returns to the same allowlisted review/workbench page and ticket fragment. External return destinations are rejected. No credential or authentication bypass is introduced.
4. Load and inspect a fresh account-bound broker preview. Preparing a manual handoff does not place an order, grant an execution token or create a native Robinhood approval.
5. Copy the exact ticket (or select its visible fallback). It includes contract identities, actions, quantities/ratios, price, session, duration, proposal identity and timestamps. Check every field and current conditions in Robinhood. The homepage link does not prefill anything.
6. Place the order only in Robinhood. Afterwards provide its exact broker order ID. The existing read-only matcher must verify account, reviewed terms, time window and user-origin evidence. No approximate candidate is automatically linked.
7. Journal proposal/review and broker observation separately. Partial fill, filled, canceled-after-partial-fill and expiry are distinct. Stale or incomplete observations are unknown. A premium credit or single entry order is not realized P/L; this journal does not calculate realized profit.

## Paper acceptance test

Open `/workbench` and sign in with the existing owner account. Select **Test an option ticket**, review its explicitly synthetic TEST contract, acknowledge paper-only and mark reviewed or reject before the five-minute expiry. It remains unfilled and never reaches a broker. A test notification must arrive on the actual intended phone and open the same ticket after sign-in. Provider acceptance alone is not device receipt.

Synthetic tests forbid live socket/HTTP/broker calls. They exercise creation, immutable review, duplicate handling, expiry and notification payload. Local test sessions are only test fixtures and must never be used as production authentication.

## Freshness interpretation

`/api/market` preserves quote source age independently from receipt age. Configured exchange-calendar context describes regular equity sessions only, with explicit provenance; it is not broker-verified tradability, holiday override or extended-session eligibility. Options sessions remain unknown without contract-specific evidence. Outside regular hours, a previous-session quote remains historical/stale, not executable. A market reopening does not by itself endorse an idea.

## Still required before claiming a live end-to-end pass

- Publish the exact reviewed local scope, run full PostgreSQL CI, then separately authorize deployment of that commit. Existing PRs remain unmerged; older deployment approval is not approval for these new changes.
- Use normal owner sign-in on the production URL. Prior browser permission denial remains in effect; no bypass or replacement credential is permitted.
- Verify an isolated hosted paper ticket and actual phone receipt/open/review. Local code tests are not that result.
- Independently verify the chosen account, relevant current market/portfolio coverage, source retrieval, model configuration/budget when used, and research mandate. No new paid subscription, credential or trading permission is assumed.
- Actual user-placed orders can be journaled only when the broker exposes the required exact-ID, origin and account evidence. Missing fields remain a blocker, not permission to relax matching.

No merge, deployment, production ticket, broker preview or financial action is part of these implementation tests.

## Local verification on October 29

Final local suite: **511 passed, 11 skipped** (PostgreSQL-only checks unavailable locally). Both new browser-independent UI suites run through pytest. Ruff, infrastructure TypeScript, JavaScript syntax, source-secret scan and whitespace checks passed. Synthetic Authlib transport exercised real OAuth state validation/replay rejection. These are code-level results, not hosted login or phone-receipt evidence. No dependency versions, deployment configuration, database schema or broker permissions changed in this workflow patch.
