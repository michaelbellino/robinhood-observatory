# Research activity visibility — 2026-10-28

This change does **not** establish broad autonomous news interpretation or live options recommendations.
It closes a visibility gap: every persisted bounded research pass, including no-trade/source failures,
now creates a privacy-preserving research-status event. The existing Web Push outbox routes it to
`/trades#research`. The authenticated report view shows the latest ten passes and explicit blockers.
Provider acceptance is not physical device receipt. No report exists if the worker never runs;
readiness must therefore also be checked, rather than treating silence as success.

`GET /api/research/readiness` is an authenticated read. Model configuration describes only the process
serving the request; web intentionally does not hold the worker's model key. It cannot establish
worker model readiness. `GET /api/research/reports` exposes only selected report fields, no credential
or SEC contact, with no-store responses. Source text is rendered as text, never HTML.

## Remaining launch gates

- Deploy matching tested web and worker commits; verify observed schema and worker progress.
- Verify selected-account authorization, fresh bound snapshots and enabled monitoring.
- Configure a bounded actual research universe with a legitimate SEC contact user agent.
- SEC submissions currently contain filing metadata only, not substantive filing/news analysis.
- Optional hosted generation needs a separately authorized API key/model/pricing/budget; a ChatGPT
  subscription or tool connection does not supply a Railway runtime API credential.
- Options discovery currently reuses collected contracts. No unsupported free delayed/scraped quote
  source should be represented as executable real-time data.
- Owner-confirmed loss/collateral limits, complete positions/open-order coverage, source timestamps,
  fresh exact option quotes, supported exits, assignment risk and account eligibility are mandatory
  for account-specific proposals. Do not substitute a small testing budget for collateral limits.
- Recheck physical phone receipt. ChatGPT MCP Events is not connected by this change.

Research-only findings may be useful without proving portfolio suitability, but this current worker
research cycle still depends on a successful broker reconciliation and runs at most every four hours
for an unchanged source configuration. That is not a breaking-news alert service. Future independent
news scheduling/content acquisition must be explicitly implemented and verified before claiming it.
