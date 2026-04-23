#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

STAMP="$(date +%Y-%m-%d-%H%M%S)"
OUTPUT_NAME="Barkboys-demo-ready-${STAMP}.zip"
OUTPUT_PATH="$(pwd)/${OUTPUT_NAME}"
TMP_ROOT="$(mktemp -d /tmp/barkboys-demo-bundle.XXXXXX)"
STAGE_DIR="${TMP_ROOT}/backend"

echo "Preparing demo bundle..."
mkdir -p "${STAGE_DIR}"

rsync -a \
  --exclude ".venv/" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  --exclude ".pytest_cache/" \
  --exclude ".mypy_cache/" \
  --exclude ".DS_Store" \
  --exclude ".git/" \
  "./" "${STAGE_DIR}/"

chmod +x "${STAGE_DIR}"/START_*.command 2>/dev/null || true

(
  cd "${TMP_ROOT}"
  /usr/bin/zip -rq "${OUTPUT_PATH}" "backend" -x "*.DS_Store"
)

rm -rf "${TMP_ROOT}"

echo ""
echo "Demo package created:"
echo "${OUTPUT_PATH}"
echo ""
echo "On MacBook Air:"
echo "1) Copy this zip over"
echo "2) Unzip"
echo "3) Open folder 'backend'"
echo "4) Double-click START_DEMO.command"
echo ""

open -R "${OUTPUT_PATH}" 2>/dev/null || true
