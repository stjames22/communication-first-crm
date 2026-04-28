#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-4174}"
HOST="${HOST:-127.0.0.1}"
BASE_URL="http://${HOST}:${PORT}"

cd "$(dirname "$0")"

echo "Installing npm dependencies..."
npm install

echo "Building project..."
npm run build

if ! python3 - <<'PY'
import importlib.util
missing = [name for name in ("fastapi", "uvicorn", "sqlalchemy") if importlib.util.find_spec(name) is None]
raise SystemExit(1 if missing else 0)
PY
then
  echo "Installing Python dependencies..."
  python3 -m pip install --user -r requirements.txt
fi

echo "Starting app on ${BASE_URL}..."
PORT="${PORT}" npm start &
server_pid="$!"

cleanup() {
  kill "${server_pid}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

for _ in $(seq 1 30); do
  if curl -fsS "${BASE_URL}/api/health" >/dev/null; then
    echo "Healthy: ${BASE_URL}/api/health"
    wait "${server_pid}"
    exit 0
  fi
  sleep 1
done

echo "Health check failed: ${BASE_URL}/api/health"
if curl -fsS "${BASE_URL}/health" >/dev/null; then
  echo "Note: ${BASE_URL}/health is healthy, but /api/health is not implemented."
fi
exit 1
