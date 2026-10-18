# Environment for exporting .pte files on this laptop (ExecuTorch source tree, no wheel install).
# Dot-source it:   . .\export_env.ps1
$EtRoot = "C:\dev\executorch"
. "$PSScriptRoot\setup_env.ps1"
$env:Path = "C:\dev\et-venv\Scripts;$EtRoot\build-arm64-windows\backends\qualcomm\RelWithDebInfo;$env:Path"
$env:PYTHONPATH = "C:\dev;$EtRoot"
$env:FLATC_EXECUTABLE = "$EtRoot\build-arm64-windows\third-party\flatc_ep\bin\flatc.exe"
Write-Host "Export env ready (python: $((Get-Command python).Source))"
