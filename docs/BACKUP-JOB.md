# Encrypted Railway backup job

This optional scheduled service uses `Dockerfile.backup` and the exact reviewed repository commit. It runs `timeout 240 python -m scripts.backup_job`, with `restartPolicyType=NEVER`, cron `0 7 * * *`, 0.25 vCPU / 0.5 GB resource limits, one replica, no public domains or TCP proxies and no persistent application process. It is not a second monitoring scheduler.

Use a dedicated private Railway bucket. Set only these service variables through secret references:

| Variable | Source |
|---|---|
| DATABASE_URL | Dedicated application's PostgreSQL DATABASE_URL |
| BACKUP_ENCRYPTION_KEY | Dedicated application's backup encryption key |
| S3_ENDPOINT / S3_BUCKET | Bucket ENDPOINT / BUCKET |
| AWS_DEFAULT_REGION | Bucket REGION |
| AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY | Bucket ACCESS_KEY_ID / SECRET_ACCESS_KEY |
| S3_URL_STYLE | Value from the bucket's connection metadata |

Do not supply ENCRYPTION_KEYS, session/MCP credentials, AI keys or brokerage credentials. The job refuses a vault decryption key. An encrypted database export necessarily contains encrypted vault records; restoring them does not expose usable broker tokens without the separate vault key.

The source transaction is read-only and consistent. The entire dump and its per-table verification manifest are encrypted before storage. The job reads the stored object back, checks its SHA-256, restores it into a new disposable PostgreSQL server, compares all table hashes, and forces restored monitoring paused/unverified. The restore server listens only on a Unix socket, with no TCP listener; no Observatory application or worker runs against it. The job itself retains the network access needed for its source DB and S3. This is not an egress-isolated recovery environment or a whole-provider disaster recovery drill.

After verification succeeds, a nonfinancial JSON summary is stored beside the encrypted backup. Only then are objects older than seven days removed from this job's dedicated prefix. Failures preserve prior backups. Whole-job timeout is four minutes; source database threshold is 32 MiB and encrypted object cap is 64 MiB. Memory use depends on logical data expansion; an out-of-memory failure must raise an operational incident and requires a streaming backup revision, not silently increasing limits. Keep the encryption key in separate owner-controlled recovery storage.

## Incremental estimate

Storage is at most roughly 0.5 GB for seven capped daily bundles, rounded to Railway's whole-GB-month billing: approximately $0.015/month at the documented $0.015/GB-month price. Daily upload volume is capped at about 1.9 GB/month; service egress is additional. Two hours/month of compute at the configured ceilings, storage and transfer are estimated below $1/month. This is a usage estimate, not a provider-enforced spending cap or permission to upgrade a shared subscription.

Source inspected October 16, 2026: https://docs.railway.com/storage-buckets/billing

## Verification and recovery

CI builds this exact operations image, exports a synthetic PostgreSQL database and restores it to the local isolated server. Production evidence must separately show `stored_and_restored`, matching table hashes, completion time and a durable verification object. A successful build or fixture run is not that evidence.

The bucket is same-provider storage. Railway outages may temporarily make it unavailable; maintain an independently stored encrypted recovery copy and a separate key inventory for provider-wide recovery. Never publish exports or detailed operational metadata to public GitHub. Never restore a backup over running production. External backup-failure notification still requires a verified channel.
