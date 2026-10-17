"""Daily bounded encrypted export, read-back verification and local isolated restore.

Runs without the app, worker, vault decryption key, brokerage or AI credentials.
The restore server listens only on a private Unix socket and is destroyed on exit.
Only opaque encrypted bundles and nonfinancial verification summaries reach S3.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
import boto3
from botocore.config import Config
from sqlalchemy import create_engine, text
from scripts.backup import backup
from scripts.restore_verify import restore

PREFIX = "observatory-encrypted/"
MAX_SOURCE_BYTES = 32 * 1024 * 1024


def command(args, **kwargs):
    return subprocess.run(args, capture_output=True, check=True, timeout=90, **kwargs)


@contextmanager
def isolated_postgres(root):
    data, socket = root / "pgdata", root / "socket"
    socket.mkdir(mode=0o700)
    command(["initdb", "-D", str(data), "--no-locale", "--encoding=UTF8", "--auth=trust"])
    command(["pg_ctl", "-D", str(data), "-l", str(root/"restore-server.log"), "-w", "start", "-o",
             f"-F -c listen_addresses='' -k {socket} -c shared_buffers=16MB -c max_connections=10"])
    try:
        name = "observatory_restore_" + uuid.uuid4().hex
        command(["createdb", "-h", str(socket), name])
        yield f"postgresql://postgres@/{name}?host={socket}"
    finally:
        command(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"])


def verify_isolated(path, root):
    # No Observatory runtime is started. This process never receives ENCRYPTION_KEYS.
    if os.getenv("ENCRYPTION_KEYS"):
        raise ValueError("Backup job must not receive broker vault decryption keys")
    with isolated_postgres(root) as target:
        os.environ["RESTORE_DATABASE_URL"] = target
        os.environ["RESTORE_ISOLATED_ACK"] = "yes"
        return restore(path)


def run():
    if os.getenv("ENCRYPTION_KEYS"):
        raise ValueError("Backup job must not receive broker vault decryption keys")
    started = time.monotonic()
    source = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(source, hide_parameters=True, connect_args={"connect_timeout":10})
    with engine.begin() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        size = connection.scalar(text("SELECT pg_database_size(current_database())"))
        if size > MAX_SOURCE_BYTES:
            raise ValueError("Source exceeded reviewed in-memory backup limit")
    client = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"], region_name=os.environ["AWS_DEFAULT_REGION"],
                          config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts":2},
                                        s3={"addressing_style":os.getenv("S3_URL_STYLE", "path")},
                                        request_checksum_calculation="when_required",
                                        response_checksum_validation="when_required"))
    bucket = os.environ["S3_BUCKET"]
    date = datetime.now(timezone.utc)
    key = PREFIX + date.strftime("%Y-%m-%dT%H%M%SZ") + "-" + uuid.uuid4().hex + ".enc"
    with tempfile.TemporaryDirectory(prefix="observatory-backup-") as directory:
        root = Path(directory)
        path = root / "backup.enc"
        backup(path)
        encrypted = path.read_bytes()
        if len(encrypted) > 64 * 1024 * 1024:
            raise ValueError("Encrypted backup exceeded storage bound")
        checksum = hashlib.sha256(encrypted).hexdigest()
        client.put_object(Bucket=bucket, Key=key, Body=encrypted, ContentType="application/octet-stream",
                          Metadata={"sha256":checksum})
        recovered = client.get_object(Bucket=bucket, Key=key)["Body"].read(len(encrypted)+1)
        if hashlib.sha256(recovered).hexdigest() != checksum:
            raise ValueError("Stored backup differs")
        path.write_bytes(recovered)
        verified = verify_isolated(path, root)
        report = {"completed_at":datetime.now(timezone.utc).isoformat(), "backup_key":key,
                  "encrypted_bytes":len(encrypted), "sha256":checksum, "source_database_bytes":size,
                  "restore":verified, "seconds":round(time.monotonic()-started, 3),
                  "isolation":"separate local postgres; Unix socket only; no app/worker/vault key"}
        client.put_object(Bucket=bucket, Key=key+".verification.json", Body=json.dumps(report).encode(),
                          ContentType="application/json")
        cutoff = date-timedelta(days=7)
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
            for item in page.get("Contents", []):
                if item["LastModified"] < cutoff and item["Key"].startswith(PREFIX):
                    client.delete_object(Bucket=bucket, Key=item["Key"])
        # Financial content, endpoints and secret values never reach logs.
        print(json.dumps({"backup":"stored_and_restored", "encrypted_bytes":len(encrypted),
                          "tables_verified":verified["tables"], "all_table_hashes_match":True,
                          "seconds":report["seconds"]}))


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(json.dumps({"backup":"failed", "error_type":type(error).__name__}))
        raise SystemExit(1) from None
