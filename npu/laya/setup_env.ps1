# Sets the env vars the NPU runner needs. Dot-source it so they stay in your shell:   . .\setup_env.ps1
# (bin\qnn_executor_runner.exe finds its qnn_executorch_backend.dll next to itself.)
$QnnSdk = "C:\Qualcomm\AIStack\QAIRT\2.46.0.260424"
if (-not (Test-Path "$QnnSdk\lib\aarch64-windows-msvc\QnnHtp.dll")) {
    Write-Error "QAIRT SDK not found at $QnnSdk"
    return
}
$env:QNN_SDK_ROOT = $QnnSdk
$env:Path = "$QnnSdk\lib\aarch64-windows-msvc;$env:Path"
$env:ADSP_LIBRARY_PATH = "$QnnSdk\lib\hexagon-v73\unsigned"   # SC8380XP = HTP v73
Write-Host "QNN env ready (QNN_SDK_ROOT=$QnnSdk)"
