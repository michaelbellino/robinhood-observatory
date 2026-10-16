# Recovery, backup and credential rotation

## Failures

| Condition | Behavior and recovery |
|---|---|
| HTTP 429 | Honor the full Retry-After (seconds or date); persist shared broker deferral and next run. No shorter retry cap overrides it. |
| Timeout/network/malformed data | Retain last successful snapshot; persist failed work and bounded exponential backoff with jitter. Never convert an absent response to an empty portfolio. |
| Expired/rejected authorization | Pause and clear verified state. Owner reauthorizes independently, selects the already bound account, resumes; reconcile before research. |
| PostgreSQL unavailable | No heartbeat, model call, or fallback in-memory scheduler. Old durable data is retained. Recover DB connectivity and inspect interrupted records. |
| Worker restart/deploy overlap | One PostgreSQL session advisory lock; all leader writes bind to that same physical connection. A lost connection cannot silently continue with a new session. Interrupted jobs are explicit. |
| AI unavailable/capped | Continue collection and heartbeat; show disabled/degraded analysis. Retain worst-case reservations even after timeout to prevent accidental budget refunds. |
| Stale feed | Distinguish broker timestamps from receipt time and synchronization age. Unknown timestamps remain unknown; dashboard labels stale last-known values. |
| Notification ambiguous | Mark delivery_unknown and do not blindly retry. This prevents duplicates but can miss a notification. Review dashboard events; choose a receiver supporting Idempotency-Key for reliable retries in a later revision. |

All timestamps are stored UTC. Dashboard times use America/New_York. The worker consults reviewed exchange calendars for known mappings; options with unverified hours keep conservative polling instead of silently assuming equity hours. Holidays, DST and early closes are tested for the reference equity calendar.

## External missed-work detection

Prepared channel: Healthchecks.io hosted free plan, one continuous check, **period 35 minutes + grace 5 minutes**, an owner-verified email/push integration with a generic title. This is an external dependency, not application hosting. It can detect a Railway-wide outage without the application sending a failure alert. Configure and verify the external account, check and delivery integration before claiming failure detection.

Set the check's ping URL as worker-only `HEARTBEAT_URL`. It is a secret. The code POSTs an empty body only after a complete validated reconciliation and durable checkpoint commit; a mere process loop never pings. Off-hours reconciliation is nominally 30 minutes plus cycle duration. Detection is approximately 40 minutes after the last successful ping, not instant. The channel contains no balances, symbols or order details.

After owner verifies a test notification, stop the worker (or block its DB in an isolated test) longer than the check threshold. Retain external check DOWN time, delivery receipt and last successful work ID. Restart; confirm one leader, reconciliation and UP. For shorter market-hour alerts use a separate externally scheduled check only after matching its holiday/early-close calendar. An intentional long pause also stops pings; explicitly manage a maintenance window on the external checker rather than emitting fake success heartbeats. Generic event webhooks are supported through `NOTIFICATION_URL`/`NOTIFICATION_TOKEN`; no destination is assumed.

## Backups

Enable daily Railway volume backups and review retention/usage after provisioning. They are same-provider backups and do not replace an isolated restore test. Keep independent encrypted logical exports under owner-controlled durable storage, with `BACKUP_ENCRYPTION_KEY` separate from exports. Never store a production export in the public repository or ephemeral container filesystem as the only copy.

`scripts.backup` exports one PostgreSQL REPEATABLE READ snapshot, a custom-format dump and a sorted per-table SHA-256/count manifest from that same snapshot. It encrypts the entire bundle using Fernet before writing mode 0600. This is intended for the initial small database; memory use grows with database size. Native PostgreSQL 17 client tools are required, or the provided pinned Docker client wrapper for local/CI use. Railway operations should run privately in the same project network.

```sh
# Required secrets are supplied by the private environment, never inline shell literals.
uv run python -m scripts.backup backups/observatory-YYYY-MM-DD.enc
# Copy the encrypted file to durable owner-controlled storage and verify its returned hash.
```

## Isolated restore

Create an empty database named `observatory_restore_<unique_suffix>` on an isolated instance with **no worker, tunnel, broker credentials in service variables, or external egress**. Set `RESTORE_DATABASE_URL` privately; keep source `DATABASE_URL` for target comparison, `BACKUP_ENCRYPTION_KEY`, and `RESTORE_ISOLATED_ACK=yes`. The script refuses the source target, an unexpected name, a nonempty target or missing acknowledgement. It does not drop existing tables.

```sh
uv run python -m scripts.restore_verify backups/observatory-YYYY-MM-DD.enc
```

It compares every restored table's count/hash, then forces paused/unverified state. Record the result and restore duration privately, not financial rows. A successful dump command alone is not a restore test. Restored credentials remain encrypted; recovery also requires the vault key. CI has passed a real encrypted round-trip restore into an isolated disposable PostgreSQL database. Production backup scheduling and restoration require their own evidence; fixture verification is not production recovery evidence.

## Rotation

- **Session secret:** replace web secret to invalidate sessions; owner signs in again. Preserve explicit owner ID.
- **MCP credentials:** generate independent high-entropy read/control tokens; store only SHA-256 hashes on web, secret values in the corresponding authenticated ChatGPT connections. Replace read and control independently; verify old token→401 and read token cannot control. Audit deployment/change outside the app as well.
- **Broker:** pause, remove local credentials, revoke the dedicated authorization at Robinhood, independently reconnect. Never rotate by extracting a token from another connector. Refresh automatically persists returned rotation; verify actual renewal behavior privately in the target deployment.
- **Vault encryption:** pause monitoring, create backup and key recovery copy. Set `ENCRYPTION_KEYS` to `new,old`, then `uv run python -m scripts.rotate_vault`; this rotates encrypted secrets, OAuth flows and account binding. Verify decrypt/read/restart before removing old key. Retain old keys securely for old encrypted backups. Never print them.
- **Backup key:** new exports use a new independent key; retain old keys alongside an access-controlled key inventory for retained exports. Never lose the only decrypt key.
- **OpenAI:** worker-only key rotation; keep AI disabled until model pricing and caps are configured. Monitoring remains independent.

Do not edit unrelated Railway projects, shared credentials or billing controls during recovery.
