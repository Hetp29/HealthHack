#!/usr/bin/env bash
# Close BLOCKERS B-007 locally: run the v2 Alembic migrations and the v2 test suite
# against a real, throwaway PostgreSQL 16 container (the RDS engine family we plan to use).
#
#   infra/local/verify_postgres.sh            # needs Docker running and the repo .venv
#
# Nothing persistent is created: the container is removed on exit, and the password is
# random per run and never printed.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"   # macOS credential helper

NAME=gt-pg-verify-$$
PORT=${PG_PORT:-55433}
PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" -e POSTGRES_USER=glucotwin -e POSTGRES_PASSWORD="$PW" \
  -e POSTGRES_DB=scratch -p "127.0.0.1:$PORT:5432" postgres:16-alpine >/dev/null
for _ in $(seq 1 30); do docker exec "$NAME" pg_isready -U glucotwin -d scratch >/dev/null 2>&1 && break; sleep 1; done
echo "postgres: $(docker exec "$NAME" psql -U glucotwin -d scratch -tAc 'show server_version')"

# shellcheck disable=SC1091
source .venv/bin/activate
URL="postgresql+psycopg://glucotwin:$PW@127.0.0.1:$PORT/scratch"

echo "== migrations: upgrade -> check -> downgrade -> upgrade"
GLIMPSE_APP_DB_URL=$URL alembic -q upgrade head
GLIMPSE_APP_DB_URL=$URL alembic check
GLIMPSE_APP_DB_URL=$URL alembic -q downgrade base
GLIMPSE_APP_DB_URL=$URL alembic -q upgrade head
echo "migrations OK (head: $(GLIMPSE_APP_DB_URL=$URL alembic current 2>/dev/null | tail -1))"

echo "== v2 suite on PostgreSQL (schema is dropped/recreated per test)"
# Known harness limitation: test_resetting_one_mode_never_touches_the_other_or_an_ordinary_account
# builds a SECOND harness mid-test; with one shared external database that second harness
# drops the first one's schema. It is deselected here and reported, not hidden.
KNOWN="tests/v2/test_demo_scenarios.py::test_resetting_one_mode_never_touches_the_other_or_an_ordinary_account"
GLIMPSE_TEST_APP_DB_URL=$URL python -m pytest tests/v2 -q -p no:cacheprovider -W ignore \
  --deselect "$KNOWN" 2>&1 | sed "s/$PW/***/g" | tail -3
echo "(deselected, shared-DB harness limitation: $KNOWN)"
