# Run the game server (:8000) and the Vite dev UI (:5173) together on Windows; Ctrl+C stops both.
# The server loads Gemma onto the NPU in the background (see qnn: in racers.yaml).
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Server = Start-Process -FilePath $Python -ArgumentList "-m", "san_francisco.server", $args `
  -WorkingDirectory $Root -NoNewWindow -PassThru
try {
  Set-Location (Join-Path $Root "frontend")
  npm run dev
} finally {
  Stop-Process -Id $Server.Id -ErrorAction SilentlyContinue
}
