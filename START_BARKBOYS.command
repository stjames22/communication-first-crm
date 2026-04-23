#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

ENV_FILE=".env"
saved_key=""
if [ -f "$ENV_FILE" ]; then
  saved_key="$(awk -F= '/^OPENAI_API_KEY=/{print substr($0, index($0, "=") + 1)}' "$ENV_FILE" | tail -n 1)"
fi

existing_pid="$(lsof -tiTCP:8000 -sTCP:LISTEN 2>/dev/null | head -n 1 || true)"

echo "BarkBoys morning launcher"
echo ""

if [ "${1:-}" = "--update-key" ]; then
  ./UPDATE_OPENAI_KEY.command
elif [ -z "$saved_key" ]; then
  echo "No saved OpenAI API key found in $ENV_FILE"
  read -r -p "Add OpenAI API key now before starting? [Y/n]: " update_key
  update_key="${update_key:-Y}"
  case "${update_key:-}" in
    y|Y|yes|YES)
      ./UPDATE_OPENAI_KEY.command
      ;;
    *)
      echo "Continuing without a saved OpenAI API key."
      ;;
  esac
else
  echo "Saved OpenAI API key found in $ENV_FILE"
fi

if [ -n "$existing_pid" ]; then
  echo ""
  echo "A process is already listening on port 8000 (PID $existing_pid)."
  read -r -p "Stop the old local BarkBoys process and start fresh? [Y/n]: " restart_existing
  restart_existing="${restart_existing:-Y}"
  case "$restart_existing" in
    y|Y|yes|YES)
      kill "$existing_pid" 2>/dev/null || true
      sleep 1
      ;;
    *)
      echo "Leaving the existing process running."
      echo "If the old window shows a stale AI badge, stop it with Control + C and rerun this launcher."
      exit 0
      ;;
  esac
fi

echo ""
echo "Starting BarkBoys..."
exec ./START_LOCAL.command
