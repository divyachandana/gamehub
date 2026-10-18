# Sets the three env vars every qnn_*.exe here needs (see README "Prerequisites").
# Dot-source it so the vars stay in your shell:   . .\setup_env.ps1
$QnnSdk = "C:\Qualcomm\AIStack\QAIRT\2.46.0.260424"
if (-not (Test-Path "$QnnSdk\lib\aarch64-windows-msvc\QnnHtp.dll")) {
    Write-Error "QAIRT SDK not found at $QnnSdk"
    return
}
$env:QNN_SDK_ROOT = $QnnSdk
$env:Path = "$PSScriptRoot\RelWithDebInfo;$QnnSdk\lib\aarch64-windows-msvc;$env:Path"
$env:ADSP_LIBRARY_PATH = "$QnnSdk\lib\hexagon-v73\unsigned"   # SC8380XP = HTP v73
Write-Host "QNN env ready (QNN_SDK_ROOT=$QnnSdk)"
