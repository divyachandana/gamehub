# NightWatch QA gate (Windows / Snapdragon laptop). From the repo root:  .\scripts\qa.ps1   [--rules] [--only static,perf] [--lan-check]
# Uses .venv so the ExecuTorch models load; falls back to python on PATH (then the gate runs on the rule scorer).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = if ($env:PY) { $env:PY } elseif (Test-Path .\.venv\Scripts\python.exe) { ".\.venv\Scripts\python.exe" } else { "python" }
& $py scripts\qa.py --python $py @args
exit $LASTEXITCODE
