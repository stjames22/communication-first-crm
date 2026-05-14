#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

STAMP="$(date +%Y-%m-%d-%H%M%S)"
OUTPUT_NAME="communication-first-crm-ready-${STAMP}.zip"
OUTPUT_PATH="$(pwd)/${OUTPUT_NAME}"
TMP_ROOT="$(mktemp -d /tmp/communication-first-crm-bundle.XXXXXX)"
PAYLOAD_DIR="${TMP_ROOT}/payload"
APP_STAGE="${TMP_ROOT}/app"
SHORTCUT="${TMP_ROOT}/Communication First CRM.command"

echo "Preparing Communication First CRM package..."
mkdir -p "${PAYLOAD_DIR}" "${APP_STAGE}"

rsync -a \
  --exclude ".venv/" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  --exclude ".pytest_cache/" \
  --exclude ".mypy_cache/" \
  --exclude ".DS_Store" \
  --exclude ".git/" \
  --exclude "communication-first-crm-ready-*.zip" \
  "./" "${APP_STAGE}/"

chmod +x "${APP_STAGE}"/START_*.command 2>/dev/null || true

(
  cd "${TMP_ROOT}"
  /usr/bin/zip -rq "${PAYLOAD_DIR}/communication-first-crm-app.zip" "app" -x "*.DS_Store"
)

cat > "${SHORTCUT}" <<'SH'
#!/bin/bash
set -euo pipefail

PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_ROOT="${HOME}/Documents/Communication First CRM"
APP_DIR="${INSTALL_ROOT}/app"
PAYLOAD_ZIP="${PACKAGE_DIR}/payload/communication-first-crm-app.zip"
PORT="${PORT:-4174}"

if [ ! -f "${PAYLOAD_ZIP}" ] && [ ! -f "${APP_DIR}/package.json" ]; then
  echo "Could not find the packaged app payload."
  echo "Expected: ${PAYLOAD_ZIP}"
  exit 1
fi

mkdir -p "${INSTALL_ROOT}"

if [ -f "${PAYLOAD_ZIP}" ]; then
  echo "Installing Communication First CRM to:"
  echo "${APP_DIR}"
  rm -rf "${APP_DIR}.new"
  mkdir -p "${APP_DIR}.new"
  /usr/bin/unzip -q "${PAYLOAD_ZIP}" -d "${APP_DIR}.new"
  rm -rf "${APP_DIR}"
  mv "${APP_DIR}.new/app" "${APP_DIR}"
  rm -rf "${APP_DIR}.new"
fi

cat > "${INSTALL_ROOT}/Communication First CRM.command" <<EOF
#!/bin/bash
set -euo pipefail
cd "${APP_DIR}"
PORT="\${PORT:-4174}" npm start
EOF
chmod +x "${INSTALL_ROOT}/Communication First CRM.command"

cd "${APP_DIR}"

echo ""
echo "Communication First CRM is installed in:"
echo "${APP_DIR}"
echo ""
echo "Shortcut:"
echo "${INSTALL_ROOT}/Communication First CRM.command"
echo ""
echo "Starting on http://127.0.0.1:${PORT}/crm/workspace"
echo ""

open "http://127.0.0.1:${PORT}/crm/workspace" >/dev/null 2>&1 || true
PORT="${PORT}" npm start
SH

chmod +x "${SHORTCUT}"

(
  cd "${TMP_ROOT}"
  /usr/bin/zip -rq "${OUTPUT_PATH}" "Communication First CRM.command" "payload" -x "*.DS_Store"
)

rm -rf "${TMP_ROOT}"

echo ""
echo "Package created:"
echo "${OUTPUT_PATH}"
echo ""
echo "Use on another Mac:"
echo "1) Unzip this package anywhere."
echo "2) Double-click 'Communication First CRM.command'."
echo "3) The app installs to ~/Documents/Communication First CRM/app and runs from that subdirectory."
echo "4) Use ~/Documents/Communication First CRM/Communication First CRM.command as the shortcut next time."
echo ""

open -R "${OUTPUT_PATH}" 2>/dev/null || true
