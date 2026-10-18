# Run Laya on the NPU. Passes all arguments through to run_laya.py, e.g.
#   .\run_laya.ps1
#   .\run_laya.ps1 --state "The app crashes on login"
#   .\run_laya.ps1 --interactive --questions .\scenarios\it_helpdesk.json
. "$PSScriptRoot\setup_env.ps1"
Write-Host "Loading Laya, the first answer takes ~10 s..."
&"C:\dev\et-venv\Scripts\python.exe" "$PSScriptRoot\run_laya.py" @args
