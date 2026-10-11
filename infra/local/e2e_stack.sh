#!/usr/bin/env bash
# Bring up the full local stack for a browser end-to-end test of ONE commit:
#   PostgreSQL 16  ->  backend image (demo mode, single worker, frozen fs-1.3)  ->  frontend dev server
#
#   infra/local/e2e_stack.sh up      # prints URLs; demo password written to $STATE/demo_password (0600)
#   infra/local/e2e_stack.sh down
#
# Needs: Docker, ml/artifacts_cgmacros_fs13/ (5 frozen files), data/local/demo/ bundles, node_modules.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

STATE=${E2E_STATE:-${TMPDIR:-/tmp}/glucotwin-e2e}
NET=gt-e2e; PG=gt-e2e-pg; API=gt-e2e-api
API_PORT=${API_PORT:-18100}; FE_PORT=${FE_PORT:-5175}
FE_ORIGIN="http://localhost:$FE_PORT"

down() {
  docker rm -f "$API" "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  [ -f "$STATE/frontend.pid" ] && kill "$(cat "$STATE/frontend.pid")" 2>/dev/null || true
  rm -rf "$STATE"
}

up() {
  down; mkdir -p "$STATE"; chmod 700 "$STATE"
  SHA=$(git rev-parse --short=12 HEAD); IMG=glucotwin-backend:$SHA
  if ! docker image inspect "$IMG" >/dev/null 2>&1; then
    CTX=$(mktemp -d); git archive HEAD | tar -x -C "$CTX"
    mkdir -p "$CTX/ml/artifacts_cgmacros_fs13" && cp ml/artifacts_cgmacros_fs13/* "$CTX/ml/artifacts_cgmacros_fs13/"
    docker build -q -t "$IMG" "$CTX" >/dev/null; rm -rf "$CTX"
  fi
  PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))")
  DEMO_PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(14))")
  (umask 077; printf '%s' "$DEMO_PW" > "$STATE/demo_password")

  docker network create "$NET" >/dev/null
  docker run -d --name "$PG" --network "$NET" -e POSTGRES_USER=glucotwin -e POSTGRES_PASSWORD="$PW" \
    -e POSTGRES_DB=glucotwin postgres:16-alpine >/dev/null
  for _ in $(seq 1 30); do docker exec "$PG" pg_isready -U glucotwin >/dev/null 2>&1 && break; sleep 1; done
  DB="postgresql+psycopg://glucotwin:$PW@$PG:5432/glucotwin"
  ENV=(-e GLUCOTWIN_APP_DB_URL="$DB" -e GLUCOTWIN_DEMO_MODE=1 -e GLUCOTWIN_DEMO_PASSWORD="$DEMO_PW"
       -v "$PWD/data/local/demo:/app/data/local/demo:ro")
  docker run --rm --network "$NET" "${ENV[@]}" "$IMG" alembic -q upgrade head
  docker run --rm --network "$NET" "${ENV[@]}" "$IMG" python -m scripts.demo_seed >/dev/null
  docker run --rm --network "$NET" "${ENV[@]}" "$IMG" python -m scripts.demo_seed --scenario returning_user >/dev/null
  # Single worker: the demo replay lock is in-process.
  docker run -d --name "$API" --network "$NET" -p "127.0.0.1:$API_PORT:8000" "${ENV[@]}" \
    -e GLUCOTWIN_CORS_ORIGINS="$FE_ORIGIN" --read-only --tmpfs /tmp --tmpfs /app/var:uid=10001 "$IMG" >/dev/null
  for _ in $(seq 1 60); do curl -fsS "localhost:$API_PORT/api/v2/ready" >/dev/null 2>&1 && break; sleep 1; done

  VITE_API_BASE_URL="http://localhost:$API_PORT" VITE_DATA_MODE=live \
    nohup npx vite dev --port "$FE_PORT" --strictPort > "$STATE/frontend.log" 2>&1 &
  echo $! > "$STATE/frontend.pid"
  for _ in $(seq 1 60); do curl -fsS -o /dev/null "$FE_ORIGIN/auth" 2>/dev/null && break; sleep 1; done
  echo "image     $IMG"
  echo "api       http://localhost:$API_PORT  (ready: $(curl -s -o /dev/null -w '%{http_code}' localhost:$API_PORT/api/v2/ready))"
  echo "frontend  $FE_ORIGIN  (auth: $(curl -s -o /dev/null -w '%{http_code}' $FE_ORIGIN/auth))"
  echo "demo pw   $STATE/demo_password"
}

case "${1:-up}" in up) up ;; down) down ;; *) echo "usage: $0 up|down"; exit 2 ;; esac
