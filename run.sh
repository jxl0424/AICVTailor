#!/usr/bin/env bash
#
# One command to run everything: backend, frontend, database, browser.
# Idempotent -- safe to run repeatedly; it only does setup work that is missing.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"

# --- config -----------------------------------------------------------------
if [[ ! -f .env ]]; then
  echo "==> no .env found, copying .env.example"
  cp .env.example .env
  echo "    edit .env to add your NVIDIA_API_KEY (free from build.nvidia.com)"
fi

# Read the handful of values this script needs. Deliberately narrow: the app
# itself reads .env properly via pydantic-settings.
get_env() {
  local key="$1" default="$2" value
  value="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  echo "${value:-$default}"
}

BACKEND_HOST="$(get_env BACKEND_HOST 127.0.0.1)"
BACKEND_PORT="$(get_env BACKEND_PORT 8000)"
FRONTEND_PORT="$(get_env FRONTEND_PORT 5173)"
OPEN_BROWSER="$(get_env OPEN_BROWSER true)"

# --- setup ------------------------------------------------------------------
if [[ ! -x "$PY" ]]; then
  echo "==> creating virtualenv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
fi

if ! "$PY" -c "import aicvtailor" >/dev/null 2>&1; then
  echo "==> installing backend"
  "$VENV/bin/pip" install --quiet -e ".[dev]"
fi

if [[ ! -d frontend/node_modules ]]; then
  echo "==> installing frontend dependencies"
  (cd frontend && npm install --silent)
fi

echo "==> preparing database"
"$VENV/bin/aicvtailor" init-db

echo "==> component check"
"$VENV/bin/aicvtailor" doctor || true

# --- run --------------------------------------------------------------------
# npm and uvicorn --reload both spawn grandchildren, so killing the pid we
# recorded leaves the real server orphaned still holding its port. Walk the tree.
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child"
  done
  kill -TERM "$pid" 2>/dev/null || true
}

PIDS=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do
    kill_tree "$pid"
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Vite runs with strictPort, so a stale process holding the port fails with an
# unhelpful message. Check first and say something actionable.
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }
for pair in "$BACKEND_PORT backend" "$FRONTEND_PORT frontend"; do
  set -- $pair
  if port_in_use "$1"; then
    echo "error: port $1 ($2) is already in use." >&2
    echo "       an earlier ./run.sh may still be running." >&2
    exit 1
  fi
done

echo "==> backend  http://${BACKEND_HOST}:${BACKEND_PORT}"
"$VENV/bin/uvicorn" aicvtailor.main:app \
  --host "$BACKEND_HOST" --port "$BACKEND_PORT" --reload \
  --reload-dir backend/src &
PIDS+=($!)

echo "==> frontend http://localhost:${FRONTEND_PORT}"
(cd frontend && BACKEND_PORT="$BACKEND_PORT" FRONTEND_PORT="$FRONTEND_PORT" npm run dev --silent) &
PIDS+=($!)

# Wait for the frontend to answer before opening a tab, so the browser does not
# land on a connection error.
if [[ "$OPEN_BROWSER" == "true" ]]; then
  for _ in $(seq 1 40); do
    if curl -sf "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
      for opener in xdg-open open; do
        if command -v "$opener" >/dev/null 2>&1; then
          "$opener" "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1 &
          break
        fi
      done
      break
    fi
    sleep 0.25
  done
fi

echo "==> running. Ctrl-C stops both."
wait
