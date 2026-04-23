#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

BUNDLED_PYTHON="./.python-runtime/bin/python3.12"
SELECTED_PYTHON=""

python_version_ok() {
  local python_bin="$1"
  "$python_bin" - <<'PY' >/dev/null 2>&1
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
}

select_python_bin() {
  if [ -x "$BUNDLED_PYTHON" ] && python_version_ok "$BUNDLED_PYTHON"; then
    printf "%s" "$BUNDLED_PYTHON"
    return
  fi
  if command -v python3 >/dev/null 2>&1 && python_version_ok "$(command -v python3)"; then
    printf "%s" "$(command -v python3)"
    return
  fi
  return 1
}

if ! SELECTED_PYTHON="$(select_python_bin)"; then
  echo "BarkBoys needs Python 3.10 or newer."
  echo "No compatible Python interpreter was found in this folder or on this Mac."
  exit 1
fi

detect_lan_ip() {
  local ip=""
  ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if [ -z "$ip" ]; then
    ip="$(ipconfig getifaddr en1 2>/dev/null || true)"
  fi
  printf "%s" "$ip"
}

ensure_openai_ca_bundle() {
  mkdir -p "./certs"

  local detected_cafile=""
  local detected_capath=""
  detected_cafile="$("$SELECTED_PYTHON" - <<'PY'
import os
import ssl
paths = ssl.get_default_verify_paths()
print(paths.openssl_cafile or "")
PY
)"
  detected_capath="$("$SELECTED_PYTHON" - <<'PY'
import os
import ssl
paths = ssl.get_default_verify_paths()
print(paths.openssl_capath or "")
PY
)"

  if [ -n "${GS_OPENAI_CA_BUNDLE:-}" ] && [ -f "${GS_OPENAI_CA_BUNDLE}" ]; then
    return
  fi

  if [ -n "$detected_cafile" ] && [ -f "$detected_cafile" ]; then
    return
  fi

  if [ -n "$detected_capath" ] && [ -d "$detected_capath" ]; then
    return
  fi

  local system_roots="./certs/macos-system-roots.pem"
  if security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > "$system_roots" 2>/dev/null \
    && [ -s "$system_roots" ]; then
    export GS_OPENAI_CA_BUNDLE="$system_roots"
    echo "BarkBoys repaired Python HTTPS trust using macOS system roots: $GS_OPENAI_CA_BUNDLE"
  else
    rm -f "$system_roots"
  fi
}

check_python_modules() {
  local python_bin="$1"
  "$python_bin" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = ["fastapi", "uvicorn", "sqlalchemy", "reportlab", "multipart", "pydantic"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(0 if not missing else 1)
PY
}

if [ -d ".venv" ] && [ ! -x ".venv/bin/python3" ]; then
  echo "Detected invalid .venv (broken interpreter path). Recreating..."
  rm -rf .venv
fi

if [ -x ".venv/bin/python3" ] && ! .venv/bin/python3 -c "import sys; print(sys.executable)" >/dev/null 2>&1; then
  echo "Detected unusable .venv. Recreating..."
  rm -rf .venv
fi

if [ ! -d ".venv" ]; then
  "$SELECTED_PYTHON" -m venv .venv
fi

source .venv/bin/activate

if ! check_python_modules python; then
  echo "Installing Python dependencies for BarkBoys..."
  if ! python -m pip install -r requirements.txt; then
    echo ""
    echo "BarkBoys could not finish dependency setup."
    echo "This machine needs internet access for the first run, or a prebuilt .venv copied from another Mac with the same Python version."
    echo "After connectivity is restored, rerun START_LOCAL.command."
    echo ""
    exit 1
  fi
fi

if ! check_python_modules python; then
  echo ""
  echo "BarkBoys dependencies are still unavailable after setup."
  echo "Please verify the virtual environment in ./backend/.venv and rerun START_LOCAL.command."
  echo ""
  exit 1
fi

ensure_openai_ca_bundle

export GS_DATABASE_URL="${GS_DATABASE_URL:-sqlite:///./barkboys_estimator.db}"
export GS_API_KEY="${GS_API_KEY:-barkboys-test-key}"
export GS_ESTIMATOR_USER="${GS_ESTIMATOR_USER:-demo}"
export GS_ESTIMATOR_PASSWORD="${GS_ESTIMATOR_PASSWORD:-demo123}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-${GS_OPENAI_API_KEY:-}}"
export GS_OPENAI_API_KEY="${GS_OPENAI_API_KEY:-${OPENAI_API_KEY:-}}"
export GS_OPENAI_VISION_MODEL="${GS_OPENAI_VISION_MODEL:-gpt-4.1}"
export GS_ALLOW_FALLBACK_HANDWRITTEN_MEASUREMENT_OCR="${GS_ALLOW_FALLBACK_HANDWRITTEN_MEASUREMENT_OCR:-1}"

python -m scripts.init_db
python -m scripts.seed_demo

echo ""
echo "BarkBoys sales quote tool is starting."
echo "Demo Hub URL: http://localhost:8000/demo"
echo "Sales Quote Tool URL: http://localhost:8000/staff-estimator"
echo "Public Estimator URL: http://localhost:8000/public-estimator"
echo "Estimator login: ${GS_ESTIMATOR_USER} / ${GS_ESTIMATOR_PASSWORD}"
echo "Database: ${GS_DATABASE_URL}"
python -m scripts.openai_preflight || true
LAN_IP="$(detect_lan_ip)"
if [ -n "${LAN_IP}" ]; then
  echo "Field test URL on same Wi-Fi: http://${LAN_IP}:8000/staff-estimator"
  echo "Public intake on same Wi-Fi: http://${LAN_IP}:8000/public-estimator"
  echo "If another device cannot connect, allow incoming connections for Terminal/Python in macOS Firewall."
fi
echo ""

open "http://localhost:8000/demo" || true
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
