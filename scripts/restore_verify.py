"""Verify every restored table against the encrypted consistent-snapshot manifest."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from scripts.backup import pg_env, manifest


def restore(path):
    source, target = os.environ["DATABASE_URL"], os.environ["RESTORE_DATABASE_URL"]
    src, dst = pg_env(source), pg_env(target)
    if tuple(src[k] for k in ("PGHOST", "PGPORT", "PGDATABASE")) == tuple(
        dst[k] for k in ("PGHOST", "PGPORT", "PGDATABASE")
    ):
        raise ValueError("Restore target is production")
    if os.getenv("RESTORE_ISOLATED_ACK") != "yes" or not dst["PGDATABASE"].startswith("observatory_restore_"):
        raise ValueError("Explicit isolated restore database required")
    bundle = json.loads(Fernet(os.environ["BACKUP_ENCRYPTION_KEY"].encode()).decrypt(Path(path).read_bytes()))
    if bundle["version"] != 1:
        raise ValueError("Unsupported backup format")
    engine = create_engine(target.replace("postgresql://", "postgresql+psycopg://"), hide_parameters=True)
    with engine.connect() as c:
        if c.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")):
            raise ValueError("Restore target must be empty")
    subprocess.run(
        ["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", dst["PGDATABASE"]],
        input=base64.b64decode(bundle["dump"]),
        env=dst,
        capture_output=True,
        check=True,
    )
    with engine.begin() as c:
        c.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        actual = manifest(c)
        verified = actual == bundle["manifest"]
        # Isolated environment must have no worker/network access while verification runs.
        c.execute(
            text("UPDATE monitor_state SET paused=true, account_verified=false, status='restored_isolated'")
        )
    if not verified:
        raise ValueError("Restored table manifest differs")
    result = {"restore": "verified", "tables": len(actual), "all_table_hashes_match": True, "paused": True}
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    try:
        restore(sys.argv[1])
    except Exception:
        print("Restore failed; details suppressed to protect credentials", file=sys.stderr)
        sys.exit(1)
