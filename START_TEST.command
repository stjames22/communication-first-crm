#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

detect_lan_ip() {
  local ip=""
  ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if [ -z "$ip" ]; then
    ip="$(ipconfig getifaddr en1 2>/dev/null || true)"
  fi
  printf "%s" "$ip"
}

docker compose up --build -d

echo ""
echo "BarkBoys estimator test environment is starting."
echo "Staff Estimator URL: http://localhost:8000/staff-estimator"
echo "Public Estimator URL: http://localhost:8000/public-estimator"
echo "Estimator login: demo / demo123"
LAN_IP="$(detect_lan_ip)"
if [ -n "${LAN_IP}" ]; then
  echo "Field test URL on same Wi-Fi: http://${LAN_IP}:8000/staff-estimator"
  echo "Public intake on same Wi-Fi: http://${LAN_IP}:8000/public-estimator"
  echo "If another device cannot connect, allow incoming connections for Docker Desktop in macOS Firewall."
fi
echo ""

open "http://localhost:8000/staff-estimator" || true
