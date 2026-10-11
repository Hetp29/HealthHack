#!/usr/bin/env bash
# H2 local container acceptance: build the real image and prove it behaves before any
# cloud deployment.
#
#   infra/local/container_smoke.sh
#
# Requires: Docker running; the five frozen files in ml/artifacts_cgmacros_fs13/ (from
# Ruhaan, never trained here). Optional: data/local/demo/*.json bundles for the demo run.
#
# Checks, in order (stops at the first failure):
#   1. artifact files match the frozen checksums (before any build)
#   2. image builds from a clean `git archive` of HEAD + the artifact only
#   3. start-time gate: a container told to expect a different checksum exits non-zero
#   4. PostgreSQL + `alembic upgrade head` as a one-off container (never at app boot)
#   5. API container: /health, /api/v2/ready, fs-1.3 model info, integrity flag, 401 on /me
#   6. scripts/smoke_v2.py full journey over HTTP
#   7. (if bundles present) demo verify + seed + smoke_demo.py in a GLUCOTWIN_DEMO_MODE container
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

ART=ml/artifacts_cgmacros_fs13
SHA=$(git rev-parse --short=12 HEAD)
IMG=glucotwin-backend:$SHA
NET=gt-smoke-$$
PG=gt-smoke-pg-$$
API=gt-smoke-api-$$
PORT=${API_PORT:-18000}
PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
CTX=$(mktemp -d)
cleanup() {
  docker rm -f "$API" "$API-demo" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$CTX"
}
trap cleanup EXIT
step() { printf '\n== %s\n' "$*"; }

step "1. frozen artifact checksums"
python3 - "$ART" <<'EOF'
import hashlib, pathlib, sys
art = pathlib.Path(sys.argv[1])
want = {
    "model.joblib": "12ed06026f34eae252fda5a837ff404cb91a4d0d56253fe8d1fc6b43597415fe",
    "model_manifest.json": "e1b4d4c1c3a8ad5f5c9b6979dca5cc953eca5192c67f97c03ca8b546686599d9",
    "metrics.json": "d519359621baee9891faef761a2f15dfe7b8f5813a16da0cc37c98fb7b3444a3",
    "metrics_day8_holdout.json": "d519359621baee9891faef761a2f15dfe7b8f5813a16da0cc37c98fb7b3444a3",
    "evaluation_detail_day8_holdout.json": "ec8829178554a12058b568e9ec9cd286b3241b8612b20ec1077e8a87f34e47fe",
}
bad = 0
for name, sha in want.items():
    p = art / name
    got = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "MISSING"
    ok = got == sha
    bad += not ok
    print(f"  {'ok ' if ok else 'BAD'} {name} {got[:16]}")
sys.exit(1 if bad else 0)
EOF

step "2. build $IMG from a clean export of HEAD + artifact"
git archive HEAD | tar -x -C "$CTX"
mkdir -p "$CTX/$ART" && cp "$ART"/* "$CTX/$ART/"
docker build -q -t "$IMG" "$CTX" >/dev/null
docker image inspect "$IMG" --format '  size={{.Size}} arch={{.Architecture}} user={{.Config.User}}'

step "3. start-time gate rejects a different expected checksum"
if docker run --rm -e GLUCOTWIN_EXPECTED_MODEL_SHA256=0000000000000000000000000000000000000000000000000000000000000000 "$IMG" >/dev/null 2>&1; then
  echo "  FAIL: container served with a mismatched checksum"; exit 1
fi
echo "  ok: container refused to start"

step "4. PostgreSQL + one-off migration"
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" -e POSTGRES_USER=glucotwin -e POSTGRES_PASSWORD="$PW" \
  -e POSTGRES_DB=glucotwin postgres:16-alpine >/dev/null
for _ in $(seq 1 30); do docker exec "$PG" pg_isready -U glucotwin >/dev/null 2>&1 && break; sleep 1; done
DB="postgresql+psycopg://glucotwin:$PW@$PG:5432/glucotwin"
docker run --rm --network "$NET" -e GLUCOTWIN_APP_DB_URL="$DB" "$IMG" alembic -q upgrade head
echo "  ok: migrated"

run_api() {  # name, extra docker args...
  local name=$1; shift
  docker run -d --name "$name" --network "$NET" -p "127.0.0.1:$PORT:8000" \
    -e GLUCOTWIN_APP_DB_URL="$DB" -e GLUCOTWIN_CORS_ORIGINS="http://localhost:5173" \
    --read-only --tmpfs /tmp --tmpfs /app/var:uid=10001 "$@" "$IMG" >/dev/null
  for _ in $(seq 1 60); do curl -fsS "localhost:$PORT/health" >/dev/null 2>&1 && return 0; sleep 1; done
  docker logs "$name" | tail -20; return 1
}

step "5. API container probes"
run_api "$API"
B="http://localhost:$PORT"
check() { if eval "$2"; then echo "  ok: $1"; else echo "  FAIL: $1"; exit 1; fi; }
check "/health 200" "curl -fsS '$B/health' >/dev/null"
check "/api/v2/ready 200" "curl -fsS '$B/api/v2/ready' >/dev/null"
check "fs-1.3 loaded" "curl -fsS '$B/api/model/info' | grep -q '\"feature_set_version\":\"fs-1.3\"'"
check "integrity verified" "curl -fsS '$B/api/v2/status' | grep -q '\"integrity_verified\":true'"
check "/api/v2/me is 401" "[ \"\$(curl -s -o /dev/null -w '%{http_code}' '$B/api/v2/me')\" = 401 ]"

step "6. full v2 journey"
python3 scripts/smoke_v2.py "$B"

if ls data/local/demo/cgmacros_demo_bundle.json >/dev/null 2>&1; then
  step "7. demo mode (single container)"
  docker rm -f "$API" >/dev/null
  DEMO_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(14))")
  DEMO_ENV=(-e GLUCOTWIN_DEMO_MODE=1 -e GLUCOTWIN_DEMO_PASSWORD="$DEMO_PW"
            -v "$PWD/data/local/demo:/app/data/local/demo:ro")
  docker run --rm --network "$NET" -e GLUCOTWIN_APP_DB_URL="$DB" "${DEMO_ENV[@]}" "$IMG" python -m scripts.demo_verify
  docker run --rm --network "$NET" -e GLUCOTWIN_APP_DB_URL="$DB" "${DEMO_ENV[@]}" "$IMG" python -m scripts.demo_seed
  run_api "$API-demo" "${DEMO_ENV[@]}"
  GLUCOTWIN_DEMO_PASSWORD="$DEMO_PW" python3 scripts/smoke_demo.py "$B"
  if [ -f data/local/demo/cgmacros_returning_user_bundle.json ]; then
    step "7b. demo mode: RETURNING USER over HTTP"
    docker run --rm --network "$NET" -e GLUCOTWIN_APP_DB_URL="$DB" "${DEMO_ENV[@]}" "$IMG" \
      python -m scripts.demo_seed --scenario returning_user
    GLUCOTWIN_DEMO_PASSWORD="$DEMO_PW" python3 scripts/smoke_demo.py "$B" --scenario returning_user
  fi
  step "7c. single-worker constraint: demo API process count"
  docker exec "$API-demo" sh -c 'ps -eo args 2>/dev/null || cat /proc/[0-9]*/cmdline | tr "\0" " "' | grep -c "uvicorn" || true
else
  step "7. demo mode skipped (no data/local/demo bundles)"
fi

printf '\nALL CHECKS PASSED for %s\n' "$IMG"
