# Operating cost estimate — October 16, 2026

Generic planning estimate, not a measured customer bill or spending authorization. Keep approved budgets, account subscription details and deployment resource measurements private. Obtain concrete approval before paid provisioning; never treat another project's allowance as authorization.

Inspected Railway resource rates: RAM $10/GB-month, CPU $20/vCPU-month, volume $0.15/GB-month and outbound network $0.05/GB. Existing subscription charges and unused included usage are separate from incremental application resources.

| Resource | Planning assumption | Monthly estimate |
|---|---|---:|
| Web, persistent worker and PostgreSQL memory | 0.65–1.25 GB aggregate average | $6.50–12.50 |
| Optional supported tunnel memory | 0.05–0.10 GB average | $0.50–1.00 |
| Aggregate CPU | 0.05–0.20 vCPU average | $1.00–4.00 |
| Database/backup storage allowance | 5–10 GB | $0.75–1.50 |
| Egress | 1–5 GB | $0.05–0.25 |
| Variance allowance | Growth and restore measurements pending | Total planning range $10–25 |
| Model analysis | Default application budgets/call limits zero | $0 |
| Purchased market data | None required by this code | $0 by default |

These are assumptions, not guaranteed costs. Measure an entire session and a week before forecasting steady usage. Resource caps are not total-bill caps; include deployment overlap, storage, egress and any optional service. Keep the worker awake and reduce off-hours work only using verified instrument schedules.

Application model-cost reservations and call limits precede model requests; retain reservations on uncertain failures. A provider budget alert is not a hard cap. Hitting analysis limits must leave collection running. Rates must match the chosen model and do not bound usage outside this application.

Backup plan eligibility and destination pricing must be verified for the actual isolated deployment. Any shared plan upgrade requires its own decision. Prepare a configuration and incremental estimate before provisioning a backup destination; a CI fixture restore does not replace production backups.

Railway workspace usage limits are under Workspace → Usage → Set Usage Limits → Compute Usage. A workspace hard cap can affect unrelated projects; do not change it solely to constrain one application without explicit authorization.

Sources: [Railway plans](https://docs.railway.com/pricing/plans), [Healthchecks pricing](https://healthchecks.io/pricing/). Review data-provider costs and use rights before adding a feed; open-source client code does not provide exchange entitlements.
