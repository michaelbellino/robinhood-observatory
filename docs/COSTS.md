# Operating cost estimate — 2026-10-16

**Initial estimate; $30 first-month incremental Railway usage approved by the owner on 2026-10-16.** Provisioning is underway in the isolated project; see DEPLOYMENT-STATUS.md. The owner's brokerage test balance is not a recurring hosting allowance and remains untouched.

Railway's published resource rates inspected on this date: RAM $10/GB-month, CPU $20/vCPU-month, volume $0.15/GB-month, outbound network $0.05/GB. Existing plan charges/unused included credits are separate and may affect the bill; this estimate does not assume any credits are available or authorize changing workspace billing.

| Incremental resource | Initial assumption | Monthly estimate |
|---|---|---:|
| Web + persistent worker + PostgreSQL RAM | 0.65–1.25 GB aggregate average | $6.50–12.50 |
| Optional tunnel RAM | 0.05–0.10 GB average | $0.50–1.00 |
| Aggregate CPU | 0.05–0.20 vCPU average | $1.00–4.00 |
| Database/backup volume usage | 5–10 GB provision/usage planning allowance | $0.75–1.50 |
| Egress | 1–5 GB | $0.05–0.25 |
| Variance allowance | Growth, restore drill, measurements pending | Rounds total to $10–25 |
| OpenAI analysis | Application limits all zero; no key configured | $0 |
| External check | Healthchecks free plan, subject to account/terms verification | $0 |
| Market-data purchases | None approved or necessary for initial reads | $0 |

**Approved first-month envelope:** up to **$30 of incremental Railway usage for the first 30 days**, only for this isolated project and one temporary isolated restore test. Do not change existing subscription, shared billing settings or other projects. Reassess measured usage before renewal. AI remains $0 unless separately enabled with approved rates and application caps. This is an approval ceiling, not a promise that Railway enforces a per-project hard spending cap.

The persistent worker cannot sleep. Database memory, snapshot size and optional sidecar are estimates until the real workload runs. Record Railway average/p95 RAM/CPU, volume growth and egress after one complete session and again after a week; compare to daily cost trajectory. Do not change an account-wide Railway usage limit to protect this project if doing so could stop unrelated applications. If projections exceed approval, pause this application's collection and ask for a concrete revision.

Model limits are enforced in application code before requests using conservative maximum input/output cost reservations and a call limit. Retain the reservation on failed/uncertain calls. Configured pricing must match the selected model; an external provider price change or costs incurred outside this application are not covered by that bound. Provider budget alerts are not treated as hard caps.

Sources: [Railway plans](https://docs.railway.com/pricing/plans), [Healthchecks pricing](https://healthchecks.io/pricing/). No market-data purchase is proposed; any future paid feed needs a documented missing capability, pricing and redistribution/automation license review.

## Deployment measurements and current limits

At approximately 16:50 UTC on 2026-10-16, current memory samples were web 0.1322 GB, worker 0.1022 GB and database 0.0505 GB. These are brief paused/empty-state samples, not full-session averages or a measured monthly cost. The one-hour metrics include time before deployment and must not be extrapolated.

Only this project has resource limits: web and worker each 0.5 GB / 0.25 vCPU; database 0.25 GB / 0.25 vCPU. Aggregate maximum configured compute at the inspected prices is approximately $27.50/month, excluding storage, egress, overlap and any optional sidecar. This is not a total-bill hard cap. The database volume is 1 GB. Railway documents service builds as free.

Native backup creation/scheduling is blocked by the current Hobby plan, as verified in this database's Backups screen. Pro is $20/month versus Hobby $5/month, with $20 versus $5 included resource usage; the incremental invoice depends on aggregate workspace usage. No upgrade is authorized or performed because it changes the shared subscription. Production backup remains an explicit gate.

Usage caps are under **Workspace → Usage → Set Usage Limits → Compute Usage**. A workspace hard cap could stop unrelated projects; it has not been changed.
