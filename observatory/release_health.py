"""Public web-process release evidence, without account or credential state."""
import os
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import now


def release_evidence(db):
    supplied = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    commit = supplied.lower() if re.fullmatch(r"[a-fA-F0-9]{40}", supplied) else None
    schema_state, revisions = "unknown", []
    try:
        with db.engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                # Transaction-local limits reset when the connection is returned.
                connection.execute(text("SET LOCAL statement_timeout = '1000ms'"))
                connection.execute(text("SET LOCAL lock_timeout = '250ms'"))
            values = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 2")).scalars().all()
            if values and all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]{1,32}", value) for value in values):
                revisions = sorted(values)
                schema_state = "observed" if len(revisions) == 1 else "ambiguous"
    except SQLAlchemyError:
        # Missing schema, unavailable database and timeout all remain unknown.
        # Never expose driver errors, hostnames, connection strings or private state.
        pass
    return {"scope": "web_process_release_only", "process": "alive", "observed_at": now().isoformat(),
            "commit_sha": commit, "commit_state": "observed" if commit else "unknown",
            "commit_source": "RAILWAY_GIT_COMMIT_SHA", "schema_state": schema_state,
            "schema_revisions": revisions, "broker_or_worker_health": "not_assessed"}
