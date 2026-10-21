# User-approved order workflow

## Implementation

The owner reviews an exact proposal at `/trades`. Viewing a notification or a GET
request cannot approve or submit. The GitHub-authenticated owner session, CSRF
token, exact Origin, explicit acknowledgment, proposal version, terms hash, and
current preview hash are required by the submission endpoint. MCP research tools
can create proposals but cannot approve or submit them.

Proposals bind to the selected account's encrypted identifier and show its masked
label. Editing creates a new proposal ID/version and retires the old preview and
approval. Migration `e9210dda1201` moves previously unbound review tickets to
`needs_review`; it does not infer an account for them.

The approval audit stores the exact reviewed terms and version, authenticated
actor, time, and a unique attempt ID. Submission reserves a durable broker
`ref_id` before I/O, revalidates after rate-limit waits, then commits `transmitting`
before calling the broker. An interrupted preparation may be reviewed again;
the attempt ID prevents a delayed old handler from using that newer approval.
An ambiguous transmission is never automatically replayed.

Reconciliation accepts an exact broker order ID, or an exact broker-returned
`ref_id` when available. It never matches merely similar orders or treats an
absent result as proof that nothing was placed. Raw broker states and reported
fill quantities are retained. Stale responses cannot regress fills or replace
an already reconciled result. Accepted, partial, filled, rejected, canceled,
expired, and unresolved outcomes are distinct.

The existing encrypted Web Push outbox links generic lock-screen notifications
to a proposal or order. Device delivery is separate from order acceptance/fill.
No supported hosted Duke notification endpoint is currently configured.

## Provider and configuration boundaries

- Hosted Robinhood credentials must come from the application's independent
  OAuth connection, stored encrypted server-side. A ChatGPT Robinhood connection
  does not provide the application's access token or establish its account scope.
- [Robinhood's trading documentation](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
  says trade approvals are rolling out for MCP accounts and certain trades may
  require approval even when the setting is off. Required final confirmation is
  completed in Robinhood. The available connector metadata does not yet expose
  a verified approval-ticket response contract. This app therefore does not
  invent an approval URL or interpret `unconfirmed` as a confirmation request.
  Unrecognized submission responses remain unresolved and block resubmission.
- The exposed order-read schema does not promise `ref_id`. A timeout without an
  order ID may require Robinhood Activity/support to establish the exact result.
  Automatic recovery of that case is not a verified live capability.
- Proposal generation through the authenticated research/MCP tool exists.
  Hosted analysis currently summarizes observations only; it does not select
  trades autonomously. Its existing model, pricing, daily/monthly spend and call
  settings are preserved. Missing configuration or exhausted budget is shown as
  unavailable/disabled, never represented as completed research.
- Current account eligibility, tool input schemas, order/session compatibility,
  tradability, fractional support, and options permissions are checked before
  submission. The adapter does not add crypto, trailing-stop, or OCO support.
  Broad option input support is not proof that a particular account/provider
  accepts multi-leg orders; the broker remains authoritative.

## Verification and release

Tests use synthetic accounts and mocked broker HTTP responses. They cover
version edits, changed/expired terms and quotes, authentication/CSRF, duplicate
clicks and concurrency, account changes, broker errors, ambiguous timeouts,
partial/terminal fills, process interruption, and delayed handlers/responses.
The full Verify workflow adds an isolated PostgreSQL service, migration tests,
backup/restore smoke, dependency audit, source scan, and infrastructure typecheck.
These are not a Robinhood sandbox or proof of live broker authorization.

No live securities order is submitted by implementation or verification. The
first live transaction must originate from the owner's application approval.
Release follows `docs/DEPLOYMENT.md`: exact passing SHA, deliberate release,
web/migration before worker, then authenticated read-only health and reconciliation
checks. Until those checks complete, do not label the full workflow live-verified.
