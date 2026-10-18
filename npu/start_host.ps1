# Start one NPU model host by hand (the games start their own; this is for chatting / testing).
# Then in a second terminal:   .\npu\tools\pipe_chat_client\chat_client.ps1 -PipeName gemma4
#
#   .\npu\start_host.ps1                 # Gemma-4-E2B on pipe "gemma4"
#   .\npu\start_host.ps1 -Model qwen     # Qwen3-1.7B on pipe "qwen3"
#
# host\qnn_llm_host.exe serves newline-delimited JSON on \\.\pipe\<PipeName>; it supports
# {"cmd": "reset"} / "reset": true (fresh context) - see host\src\qnn_llm_host_reset.cpp.
param(
  [ValidateSet("gemma", "qwen")] [string]$Model = "gemma",
  [string]$PipeName,
  [int]$SeqLen
)

. "$PSScriptRoot\setup_env.ps1"
$models = @{
  gemma = @{ dir = "gemma4_e2b_sc8380xp_ctxt_4000_ar_l-128"; pte = "gemma4_e2b_sc8380xp_ctxt_4000-001.pte"; version = "gemma4"; pipe = "gemma4"; seq = 3800 }
  qwen  = @{ dir = "sc8380_qwen3-1_7b_ctxt-8000"; pte = "sc8380_qwen3-1_7b_ctxt-8000.pte"; version = "qwen3"; pipe = "qwen3"; seq = 7800 }
}
$m = $models[$Model]
$ModelDir = "$PSScriptRoot\models\$($m.dir)"

& "$PSScriptRoot\host\qnn_llm_host.exe" `
  --model_path "$ModelDir\$($m.pte)" `
  --tokenizer_path "$ModelDir\tokenizer.json" `
  --decoder_model_version $m.version `
  --pipe_name $(if ($PipeName) { $PipeName } else { $m.pipe }) `
  --seq_len $(if ($SeqLen) { $SeqLen } else { $m.seq }) `
  --performance_output_path "$env:TEMP\qnn_llm_host_speed.txt"
