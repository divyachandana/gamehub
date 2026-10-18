# Sets the three env vars every qnn_*.exe here needs (see docs/npu-setup.md).
# Dot-source it so the vars stay in your shell:   . .\npu\setup_env.ps1
param([string]$QnnSdk = "C:\Qualcomm\AIStack\QAIRT\2.46.0.260424")
if (-not (Test-Path "$QnnSdk\lib\aarch64-windows-msvc\QnnHtp.dll")) {
    Write-Error "QAIRT SDK not found at $QnnSdk"
    return
}
$env:QNN_SDK_ROOT = $QnnSdk
# host\ holds qnn_executorch_backend.dll next to qnn_llm_host.exe; QnnHtp.dll & co. live in the SDK.
$env:Path = "$PSScriptRoot\host;$QnnSdk\lib\aarch64-windows-msvc;$env:Path"
$env:ADSP_LIBRARY_PATH = "$QnnSdk\lib\hexagon-v73\unsigned"   # SC8380XP = HTP v73
Write-Host "QNN env ready (QNN_SDK_ROOT=$QnnSdk)"
