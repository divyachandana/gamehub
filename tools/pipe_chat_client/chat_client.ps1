<#
.SYNOPSIS
  Interactive console client for qnn_llm_host's named-pipe protocol.

.DESCRIPTION
  qnn_llm_host serves newline-delimited JSON over a Windows named pipe --
  nothing a browser or plain file access can speak (a pipe needs a client to
  write a line and then block waiting for the matching response line back;
  file:// just tries to read bytes). This is that client.

  Requests share one continuous conversation for the life of this script's
  connection -- the model's KV-cache position advances with every prompt you
  send, there's no per-turn reset. Exiting this script disconnects but does
  NOT stop the host (so you can reconnect later in the same conversation's
  token-budget sense... except the host itself has no "restart conversation"
  concept either -- a genuinely fresh conversation needs a fresh host
  process). Type 'shutdown' to stop the host process itself instead of just
  disconnecting.

.PARAMETER PipeName
  Must match the --pipe_name the host was started with.

.PARAMETER SeqLen
  0 (default) omits "seq_len" from every request, so the host's own --seq_len
  governs. Set explicitly to override it for this connection -- but remember
  it's a ceiling on the running position across the WHOLE conversation, not a
  per-turn budget (see qnn_llm_host.cpp's own file header). Exceeding it is a
  hard process crash, not a recoverable error -- pick a value comfortably
  under the model's compiled context length for a multi-turn session, not
  right up against it.

.PARAMETER Stream
  Print tokens as they're generated instead of waiting for the full
  response. On by default; pass -Stream:$false for the old wait-then-print
  behavior.

.EXAMPLE
  .\chat_client.ps1 -PipeName gemma4

.EXAMPLE
  # Override the host's --seq_len default for every request on this connection.
  # Remember it's a ceiling on the WHOLE conversation's running position, not
  # a per-turn budget -- see the DESCRIPTION above.
  .\chat_client.ps1 -PipeName gemma4 -SeqLen 3700
#>
param(
    [string]$PipeName = "qnn_llm_host",
    # Left unset (0) by default so requests omit "seq_len" entirely and fall
    # through to whatever the host was actually started with (--seq_len).
    # A previous version of this script defaulted this to 256 and sent it on
    # every request -- which silently overrode the host's own --seq_len flag
    # on every single turn, since a request's own seq_len always wins. Combined
    # with seq_len being a whole-connection running ceiling (not per-turn),
    # that meant a second turn could exceed it even though 256 was never
    # exceeded on its own -- generation quietly truncated on turn 1 near 256
    # tokens, then turn 2 hit qnn_llm_host's hard ET_CHECK_MSG process abort
    # ("sequence length exceeded"), despite the host being launched with a
    # much larger --seq_len that was never actually being used.
    [int]$SeqLen = 0,
    [double]$Temperature = 0.0,
    [string]$SystemPrompt = "",
    [bool]$Stream = $true
)

$pipePath = "\\.\pipe\$PipeName"
Write-Host "Connecting to $pipePath ..." -ForegroundColor DarkGray

$pipe = New-Object System.IO.Pipes.NamedPipeClientStream(".", $PipeName, [System.IO.Pipes.PipeDirection]::InOut)
try {
    $pipe.Connect(5000)
} catch {
    Write-Host "Could not connect. Is qnn_llm_host running with -pipe_name $PipeName ?" -ForegroundColor Red
    exit 1
}

$writer = New-Object System.IO.StreamWriter($pipe)
$writer.AutoFlush = $true
$reader = New-Object System.IO.StreamReader($pipe)

Write-Host "Connected. Type a prompt and press Enter." -ForegroundColor DarkGray
Write-Host "  'exit'     -- disconnect, leave the host running" -ForegroundColor DarkGray
Write-Host "  'shutdown' -- stop the host process itself" -ForegroundColor DarkGray
Write-Host ""

while ($true) {
    $prompt = Read-Host "You"

    # Read-Host on a non-interactive/exhausted input stream (piped input that
    # ran dry, redirected stdin) returns $null immediately rather than
    # blocking -- without this check that turns into a silent, instant
    # infinite loop instead of a hang. A real interactive Enter press on an
    # empty line is "", not $null, so this doesn't affect normal use.
    if ($null -eq $prompt) {
        Write-Host "No more input (stdin closed) -- exiting." -ForegroundColor DarkGray
        break
    }

    if ($prompt -eq "exit") {
        break
    }
    if ($prompt -eq "shutdown") {
        $writer.WriteLine('{"cmd": "shutdown"}')
        $respLine = $reader.ReadLine()
        Write-Host $respLine -ForegroundColor DarkGray
        break
    }
    if ([string]::IsNullOrWhiteSpace($prompt)) {
        continue
    }

    $reqObj = [ordered]@{
        prompt          = $prompt
        system_prompt   = $SystemPrompt
        temperature     = $Temperature
        include_metrics = $true
        stream          = $Stream
    }
    # Only send seq_len when explicitly overridden (see .PARAMETER SeqLen) --
    # omitting the key lets the host's own req.value("seq_len", FLAGS_seq_len)
    # fallback apply, instead of this client silently overriding it every turn.
    if ($SeqLen -gt 0) {
        $reqObj["seq_len"] = $SeqLen
    }
    $req = $reqObj | ConvertTo-Json -Compress

    $writer.WriteLine($req)

    if ($Stream) {
        # Delta lines lack an "ok" key -- that's how the terminal line (which
        # has one) is told apart from a chunk. Print deltas as they arrive,
        # then read exactly one terminal line to close out the request.
        Write-Host -NoNewline "Model: " -ForegroundColor Cyan
        $final = $null
        while ($true) {
            $line = $reader.ReadLine()
            if ($null -eq $line) {
                Write-Host ""
                Write-Host "Host closed the connection." -ForegroundColor Red
                break
            }
            $obj = $line | ConvertFrom-Json
            if ($obj.PSObject.Properties.Name -contains "delta") {
                Write-Host -NoNewline $obj.delta -ForegroundColor Cyan
            } else {
                $final = $obj
                break
            }
        }
        Write-Host ""
        if ($null -eq $final) {
            break
        }
        if (-not $final.ok) {
            Write-Host "Error: $($final.error)" -ForegroundColor Red
        } elseif ($final.metrics) {
            $m = $final.metrics
            Write-Host ("  [{0:N1} tok/s prefill, {1:N1} tok/s decode]" -f $m.prefill_token_per_sec, $m.decode_token_per_sec) -ForegroundColor DarkGray
        }
    } else {
        $respLine = $reader.ReadLine()
        if ($null -eq $respLine) {
            Write-Host "Host closed the connection." -ForegroundColor Red
            break
        }
        $resp = $respLine | ConvertFrom-Json
        if ($resp.ok) {
            Write-Host "Model: $($resp.text)" -ForegroundColor Cyan
            if ($resp.metrics) {
                $m = $resp.metrics
                Write-Host ("  [{0:N1} tok/s prefill, {1:N1} tok/s decode]" -f $m.prefill_token_per_sec, $m.decode_token_per_sec) -ForegroundColor DarkGray
            }
        } else {
            Write-Host "Error: $($resp.error)" -ForegroundColor Red
        }
    }
    Write-Host ""
}

$pipe.Dispose()
Write-Host "Disconnected." -ForegroundColor DarkGray
