#!/usr/bin/env bash
# NightWatch QA gate (macOS/Linux). From anywhere:  ./scripts/qa.sh   [--rules] [--only static,perf] [--lan-check]
# Uses .venv (or $PY) so the ExecuTorch models load; falls back to python3 (then the gate runs on the rule scorer).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}
exec "$PY" scripts/qa.py --python "$PY" "$@"
