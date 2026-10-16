#!/bin/sh
# Optional local/CI adapter when native PostgreSQL 17 client tools are absent.
# Credentials are inherited as environment variables, never command-line literals.
set -eu
case "$(basename "$0")" in pg_dump|pg_restore) operation="$(basename "$0")";; *) exit 2;; esac
exec docker run --rm -i --network "${PGTOOLS_NETWORK:-host}" \
  --env PGHOST --env PGPORT --env PGUSER --env PGPASSWORD --env PGDATABASE --env PGCONNECT_TIMEOUT --env PGSSLMODE \
  postgres:17.7-bookworm@sha256:86e0b703649d7a792bd9243ee28afc9d8f7c6b2b5638077c9d6882d4d472bbfd \
  "$operation" "$@"
