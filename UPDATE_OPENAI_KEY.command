#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE=".env"
touch "$ENV_FILE"

read -r -s -p "Paste new OpenAI API key: " OPENAI_KEY
echo ""

if [ -z "${OPENAI_KEY}" ]; then
  echo "No key entered. Nothing changed."
  exit 1
fi

python3 - "$ENV_FILE" "$OPENAI_KEY" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
new_key = sys.argv[2]

existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
lines = existing.splitlines()
updated = False
result = []

for line in lines:
    if line.startswith("OPENAI_API_KEY="):
        result.append(f"OPENAI_API_KEY={new_key}")
        updated = True
    else:
        result.append(line)

if not updated:
    if result and result[-1].strip():
        result.append("")
    result.append(f"OPENAI_API_KEY={new_key}")

env_path.write_text("\n".join(result) + "\n", encoding="utf-8")
PY

echo "Saved OpenAI API key to $ENV_FILE"
echo ""
echo "RESTART REQUIRED:"
echo "  Fully stop BarkBoys if it is already running."
echo "  Then start it again so the new API key is loaded."
echo ""
echo "Next step:"
echo "  ./START_LOCAL.command"
