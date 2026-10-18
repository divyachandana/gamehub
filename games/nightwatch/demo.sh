#!/usr/bin/env bash
# One-command NightWatch demo on macOS/Linux. From the repo root:  ./demo.sh   or   PIN=246810 ./demo.sh
# Picks NPU (QNN) models if present, else CPU (XNNPACK); starts the server on the local network and opens the screens.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}
pick() { if [ -f "models/$1" ]; then echo "models/$1 NPU"; else echo "models/$2 CPU"; fi; }
read -r PM PD <<<"$(pick normal_pattern_qnn.pte normal_pattern_xnnpack.pte)"
if [ -f models/person_ssdlite_qnn_int8.pte ]; then CM=models/person_ssdlite_qnn_int8.pte; CD=NPU
else read -r CM CD <<<"$(pick person_ssdlite_qnn.pte person_ssdlite_xnnpack.pte)"; fi
echo "Pattern model: $PM on $PD | Camera model: $CM on $CD"
"$PY" nightwatch/server.py --lan --l1-timeout 20 --ack-timeout 15 --model "$PM" --device "$PD" \
  --camera-model "$CM" --camera-device "$CD" ${PIN:+--pin "$PIN"} &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 4
OPEN=$(command -v open || command -v xdg-open)
for page in dashboard sim camera; do "$OPEN" "http://localhost:8000/$page/" >/dev/null 2>&1 || true; done
IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
echo; echo "Staff phones (same Wi-Fi): http://$IP:8000/staff/   PIN: on the dashboard and above."
echo "Ctrl+C to stop."; wait $SERVER
