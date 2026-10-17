"""Encrypted consistent PostgreSQL dump plus a manifest from the same exported snapshot."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse, unquote, parse_qs
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text


def pg_env(url):
    parts = urlparse(url.replace("postgresql+psycopg", "postgresql"))
    if parts.scheme != "postgresql":
        raise ValueError("PostgreSQL URL required")
    return {
        **os.environ,
        "PGHOST": parse_qs(parts.query).get("host", [parts.hostname or "localhost"])[0],
        "PGPORT": str(parts.port or 5432),
        "PGUSER": unquote(parts.username or ""),
        "PGPASSWORD": unquote(parts.password or ""),
        "PGDATABASE": parts.path.lstrip("/"),
        "PGCONNECT_TIMEOUT": "10",
        "PGSSLMODE": parse_qs(parts.query).get("sslmode", ["prefer"])[0],
    }


def manifest(connection):
    names = (
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name"
            )
        )
        .scalars()
        .all()
    )
    result = {}
    for name in names:
        quoted = connection.dialect.identifier_preparer.quote(name)
        rows = connection.execute(text(f"SELECT row_to_json(t)::text FROM {quoted} t")).scalars().all()
        result[name] = {
            "count": len(rows),
            "sha256": hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest(),
        }
    return result


def backup(path):
    cipher = Fernet(os.environ["BACKUP_ENCRYPTION_KEY"].encode())
    url = os.environ["DATABASE_URL"]
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"), hide_parameters=True)
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
        with c.begin():
            c.execute(text("SET TRANSACTION READ ONLY"))
            c.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            snapshot = c.scalar(text("SELECT pg_export_snapshot()"))
            expected = manifest(c)
            dump = subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--snapshot", snapshot],
                env=pg_env(url),
                capture_output=True,
                check=True,
            ).stdout
    bundle = json.dumps(
        {"version": 1, "manifest": expected, "dump": base64.b64encode(dump).decode()}
    ).encode()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encrypted = cipher.encrypt(bundle)
    with open(target, "xb") as f:
        os.chmod(target, 0o600)
        f.write(encrypted)
    print(json.dumps({"encrypted_bytes": len(encrypted), "sha256": hashlib.sha256(encrypted).hexdigest()}))


if __name__ == "__main__":
    try:
        backup(sys.argv[1])
    except Exception:
        print("Backup failed; details suppressed to protect credentials", file=sys.stderr)
        sys.exit(1)
