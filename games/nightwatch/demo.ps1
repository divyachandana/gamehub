# One-command NightWatch demo on the Snapdragon laptop (Windows). From the repo root:
#   powershell -ExecutionPolicy Bypass -File demo.ps1            # random phone PIN
#   powershell -ExecutionPolicy Bypass -File demo.ps1 -Pin 246810
#   powershell -ExecutionPolicy Bypass -File demo.ps1 -Fast      # shorter escalation waits for a live demo
# Picks the NPU (QNN) models if they exist, else the CPU (XNNPACK) ones, starts the server on the
# local network (phones on the same Wi-Fi can open /staff/), and opens the demo screens.
param([string]$Pin = "", [switch]$Fast)
$py = if (Test-Path .\.venv\Scripts\python.exe) { ".\.venv\Scripts\python.exe" } else { "python" }
function Pick($npu, $cpu) { if (Test-Path "models\$npu") { @("models\$npu", "NPU") } else { @("models\$cpu", "CPU") } }
$pattern = Pick "normal_pattern_qnn.pte" "normal_pattern_xnnpack.pte"
$camera = if (Test-Path "models\person_ssdlite_qnn_int8.pte") { @("models\person_ssdlite_qnn_int8.pte", "NPU") } else { Pick "person_ssdlite_qnn.pte" "person_ssdlite_xnnpack.pte" }
# -Fast: shorter escalation waits for a live, time-boxed demo (heads-up 8 s, then urgent/emergency every 6 s).
$l1 = if ($Fast) { "8" } else { "20" }; $ack = if ($Fast) { "6" } else { "15" }
$serverArgs = @("nightwatch\server.py", "--lan", "--l1-timeout", $l1, "--ack-timeout", $ack,
          "--model", $pattern[0], "--device", $pattern[1], "--camera-model", $camera[0], "--camera-device", $camera[1])
if ($Pin) { $serverArgs += @("--pin", $Pin) }
Write-Host "Pattern model: $($pattern[0]) on $($pattern[1]) | Camera model: $($camera[0]) on $($camera[1])"
$server = Start-Process -FilePath $py -ArgumentList $serverArgs -PassThru -NoNewWindow
Start-Sleep -Seconds 4
foreach ($page in "dashboard", "sim", "camera") { Start-Process "http://localhost:8000/$page/" }
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -match "Wi-Fi|Wireless" } | Select-Object -First 1).IPAddress
Write-Host "`nStaff phones (same Wi-Fi): http://$($ip):8000/staff/   PIN is on the dashboard (bottom left) and above."
Write-Host "Press Enter to stop the demo."
[void][Console]::ReadLine()
Stop-Process -Id $server.Id
