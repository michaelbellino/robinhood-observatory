# Paper workbench

Open `/workbench` on the existing authenticated web service. This adds no service,
model call, market-data subscription or brokerage request. Worker cadence and
Railway resource limits are unchanged. The runtime remains observe-only.

## What to test

1. Sign in using the existing owner account. Open **Paper workbench** from the dashboard.
2. Send `status`, `portfolio`, `quotes`, or `limits`. These read stored data and retain
   timestamps and coverage warnings. `help` lists the exact supported commands.
3. Send `paper equity` or `paper option`. Each creates an explicitly synthetic $1
   ticket for the invented symbol `TEST`. The option example uses one contract at
   $0.01 with an explicit 100 multiplier. It is not an actual available contract.
4. Review the symbol, side, quantity, limit, fees, time in force and option terms.
   Acknowledge the paper-only label, then mark reviewed or reject. A review remains
   **unfilled** and is never sent to a broker.
5. Use the custom form to test the $5 per-ticket limit. The server rejects a ticket
   above the cap, including the fee allowance. Ten reservations or $20 hypothetical
   debit exhaust the daily test budget until New York midnight. Rejected and
   expired tickets do not refund the reservation.
6. Wait beyond the five-minute deadline, then attempt review: it must be rejected.
   To change terms, create a new ticket. Retrying the same request ID and exact
   terms returns the original record; reusing it with different terms fails.

The command assistant is deterministic, not an LLM. Unknown messages take no
action. `pause` and `resume` use the existing audited monitoring control; neither
cancels orders nor closes positions. Free-form research, strategy selection and
real order execution are not implemented by this feature.

## Isolation and authorization

`review_rehearsals` is separate from live snapshots, orders and the pre-existing
options research proposals. All test inputs are hypothetical, even if a real symbol
is typed. No live quote, buying-power assertion, execution support or fill is inferred.
The record includes an immutable hash of all displayed terms and its expiry.
Paper limits are constants in `observatory/rehearsal.py`, not real-money permissions.

The REST routes use the application's existing owner authentication. Read tokens
may inspect commands and tickets but cannot create them. Control tokens can create
test tickets but cannot acknowledge reviews. Review requires an authenticated owner
dashboard session plus CSRF/origin validation. This separates automation credentials
from the review interface; **a session is not proof that a human personally clicked**.
No real-money authorization can be derived from any paper review record.

The database serializes idempotency and daily budget reservations using the same
state-row lock across web processes. Calls with uncertain responses must retain
their request ID and payload. Amounts use decimal arithmetic rounded upward to
eight decimal places. Option quantities are limited to one long contract; short
options, combinations, assignment and exercise are outside this test workflow.

## Deployment

Migration `b6217cdf403a` adds one table and index. Release only the web service after
Verify passes, pinned to the tested commit. No worker restart or settings change is
needed. Keep the previous web SHA as the rollback target; leave the additive table
in place on rollback. Run the source scan and full CI including real PostgreSQL
concurrency, migration and backup/restore tests. Local SQLite skips are not a
substitute for those gates.

## Next implementation boundaries

Keep strategy logic separate and versioned. Before any real-money integration,
establish complete relevant broker/order coverage, exact supported order types,
fresh executable quotes, account and options permissions, an independently designed
execution/reconciliation state machine and an explicit human review process. This
release establishes none of those execution claims. Broad limits alone and a paper
review must never be converted into authorization for an unseen live order.

Optional OpenAI analysis remains off unless a key, model, current price assumptions
and explicit nonzero daily/monthly budgets are configured separately. No secret
should be pasted into chat or committed. A ChatGPT MCP enrollment is also separate
from the working owner dashboard and Railway collector.
