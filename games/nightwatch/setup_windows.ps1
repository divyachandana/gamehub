# NightWatch setup for the Snapdragon X Elite laptop (Windows on ARM). Run in PowerShell from the repo root:
#   powershell -ExecutionPolicy Bypass -File setup_windows.ps1
# Uses the "py" launcher. If torch/executorch have no ARM64 wheel for your Python, install an x64 Python
# (it runs under Windows' emulation) and re-run with:  $env:NW_PY = "py -3.12-64"
$py = if ($env:NW_PY) { $env:NW_PY } else { "py -3" }
Write-Host "Using: $py"
Invoke-Expression "$py -m venv .venv"
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python nightwatch\check.py
Write-Host "`nIf every model line says OK, start the server with the command printed above."
