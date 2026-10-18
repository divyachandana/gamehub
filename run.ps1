<#
.SYNOPSIS
  Start the Game Hub (http://localhost:8000) and open it in the browser.

.DESCRIPTION
  The hub starts Inference Rally (:8001) or San Francisco (:8002) when you pick one, loading the
  models on the NPU. Ctrl+C stops the hub, the running game and the NPU model hosts.
  Run .\setup.ps1 once first.
#>
param([switch]$NoBrowser)
$Root = $PSScriptRoot
$python = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  Write-Host "No .venv yet: run .\setup.ps1 first." -ForegroundColor Yellow
  exit 1
}
if (-not $NoBrowser) {
  # Give the hub a moment to bind its port before the browser asks for the page.
  Start-Job { Start-Sleep 2; Start-Process "http://localhost:8000" } | Out-Null
}
& $python "$Root\hub\server.py"
