<#
.SYNOPSIS
  One-time setup of the Game Hub on a Snapdragon X Elite laptop (Windows on ARM).

.DESCRIPTION
  1. Python env (.venv, ARM64 Python 3.12) with both games installed.
  2. Builds the racing games' frontends and installs Alibi Breaker's packages (Node.js / npm).
  3. Writes this clone's paths (NPU bundle, Laya, QAIRT SDK, Laya's Python) into the racing
     games' racers.yaml, and the QAIRT SDK into Alibi Breaker's .env.
  4. Checks every NPU prerequisite and says what is missing and where to get it
     (docs/models.md, docs/npu-setup.md). Missing NPU pieces don't stop the games: they still
     run with simulated racers.

  Re-run it any time; -CheckOnly skips steps 1-2.

.EXAMPLE
  .\setup.ps1
.EXAMPLE
  .\setup.ps1 -QnnSdk "D:\QAIRT\2.46.0.260424" -LayaPython "C:\dev\et-venv\Scripts\python.exe"
#>
param(
  [string]$QnnSdk = "C:\Qualcomm\AIStack\QAIRT\2.46.0.260424",
  # A Python with torch + transformers + numpy: Laya's tokenizer and host code need them.
  [string]$LayaPython = "C:\dev\et-venv\Scripts\python.exe",
  [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Games = @("inference-rally", "san-francisco")

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

if (-not $CheckOnly) {
  Step "Python env (.venv) with both games"
  if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) { py -3.12-arm64 -m venv "$Root\.venv" }
  & "$Root\.venv\Scripts\python.exe" -m pip install -q --upgrade pip
  & "$Root\.venv\Scripts\python.exe" -m pip install -q -e "$Root\games\inference-rally[dev]" -e "$Root\games\san-francisco"

  foreach ($game in $Games) {
    Step "Frontend: $game"
    Push-Location "$Root\games\$game\frontend"
    npm install --no-audit --no-fund
    npm run build
    Pop-Location
  }

  Step "Alibi Breaker (Node.js)"
  Push-Location "$Root\games\alibi-breaker"
  npm install --no-audit --no-fund
  Pop-Location
}

Step "Paths in racers.yaml"
$fwd = { param($p) $p -replace '\\', '/' }
foreach ($game in $Games) {
  $file = "$Root\games\$game\racers.yaml"
  # .NET reads and writes UTF-8 (Get-Content in Windows PowerShell 5.1 would assume ANSI).
  $text = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)
  $text = $text -replace '(?m)^  bundle_dir: .*$', "  bundle_dir: $(& $fwd "$Root\npu")   # setup.ps1 sets this to your clone"
  $text = $text -replace '(?m)^  laya_dir: .*$', "  laya_dir: $(& $fwd "$Root\npu\laya")   # setup.ps1 sets this to your clone"
  $text = $text -replace '(?m)^  qnn_sdk: .*$', "  qnn_sdk: $(& $fwd $QnnSdk)"
  $text = $text -replace '(?m)^  python: .*$', "  python: $(& $fwd $LayaPython)   # a Python with torch + transformers (setup.ps1 -LayaPython)"
  [System.IO.File]::WriteAllText($file, $text)
  Write-Host "  $game\racers.yaml"
}
# Alibi Breaker finds npu/ itself; it only needs the SDK and Laya's Python (in its git-ignored .env).
$envFile = "$Root\games\alibi-breaker\.env"
$lines = @(if (Test-Path $envFile) { [System.IO.File]::ReadAllLines($envFile) | Where-Object { $_ -notmatch '^(QNN_SDK_ROOT|LAYA_PYTHON)=' } })
[System.IO.File]::WriteAllLines($envFile, [string[]]($lines + "QNN_SDK_ROOT=$(& $fwd $QnnSdk)" + "LAYA_PYTHON=$(& $fwd $LayaPython)"))
Write-Host "  alibi-breaker\.env"

Step "NPU prerequisites"
$gemma = "$Root\npu\models\gemma4_e2b_sc8380xp_ctxt_4000_ar_l-128"
$qwen = "$Root\npu\models\sc8380_qwen3-1_7b_ctxt-8000"
$laya = "$Root\npu\laya"
$checks = @(
  @("QAIRT SDK (QnnHtp.dll)", "$QnnSdk\lib\aarch64-windows-msvc\QnnHtp.dll", "docs/npu-setup.md: install the QAIRT SDK"),
  @("HTP v73 skel (Snapdragon X)", "$QnnSdk\lib\hexagon-v73\unsigned", "comes with the QAIRT SDK"),
  @("NPU LLM host", "$Root\npu\host\qnn_llm_host.exe", "docs/npu-setup.md: build qnn_llm_host with the runner patch"),
  @("ExecuTorch QNN backend DLL", "$Root\npu\host\qnn_executorch_backend.dll", "built with qnn_llm_host"),
  @("Gemma-4-E2B .pte", "$gemma\gemma4_e2b_sc8380xp_ctxt_4000-001.pte", "docs/models.md"),
  @("Gemma-4-E2B tokenizer.json", "$gemma\tokenizer.json", "docs/models.md"),
  @("Qwen3-1.7B .pte", "$qwen\sc8380_qwen3-1_7b_ctxt-8000.pte", "docs/models.md"),
  @("Qwen3-1.7B tokenizer.json", "$qwen\tokenizer.json", "docs/models.md"),
  @("Laya .pte", "$laya\models\laya_typed_decisions_sc8380xp_s512\laya_typed_decisions_sc8380xp_s512_fp16.pte", "docs/models.md (or npu/laya/export_laya_qnn.py)"),
  @("Laya NPU host", "$laya\bin\qnn_static_host.exe", "docs/npu-setup.md"),
  @("Laya's Python", $LayaPython, "-LayaPython: a Python with torch + transformers")
)
$missing = 0
foreach ($c in $checks) {
  if (Test-Path $c[1]) {
    Write-Host ("  [ok]      {0}" -f $c[0]) -ForegroundColor Green
  } else {
    $missing++
    Write-Host ("  [missing] {0}  ->  {1}" -f $c[0], $c[2]) -ForegroundColor Yellow
  }
}
if (Test-Path $LayaPython) {
  & $LayaPython -c "import torch, transformers, numpy" 2>$null
  if ($LASTEXITCODE -ne 0) {
    $missing++
    Write-Host "  [missing] torch / transformers / numpy in $LayaPython" -ForegroundColor Yellow
  }
}

if ($missing) {
  Write-Host "`n$missing NPU piece(s) missing. The racing games still run with simulated racers; see docs/models.md." -ForegroundColor Yellow
} else {
  Write-Host "`nAll set: every model will run on the NPU." -ForegroundColor Green
}
Write-Host "Start the hub:  .\run.ps1   ->  http://localhost:8000"
