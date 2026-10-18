# Score a scenario file on the NPU, e.g.
#   .\run_scenario.ps1 .\scenarios\food_delivery.json
#   .\run_scenario.ps1 .\scenarios\food_delivery.json --check     # also compare with PyTorch on CPU
#   .\run_scenario.ps1 all                                        # every scenario in .\scenarios
. "$PSScriptRoot\setup_env.ps1"
$py = "C:\dev\et-venv\Scripts\python.exe"
Write-Host "Loading Laya, results appear in ~10-20 s..."
if ($args.Count -gt 0 -and $args[0] -eq "all") {
    $rest = @($args | Select-Object -Skip 1)
    Get-ChildItem "$PSScriptRoot\scenarios\*.json" | Where-Object { (Get-Content $_.FullName -Raw) -match '"cases"' } |
        ForEach-Object { & $py "$PSScriptRoot\run_scenario.py" $_.FullName @rest }
} else {
    & $py "$PSScriptRoot\run_scenario.py" @args
}
