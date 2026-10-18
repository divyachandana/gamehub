# Terminal 1 of the README quick start, with paths local to this folder.
# Then in Terminal 2:   .\tools\pipe_chat_client\chat_client.ps1 -PipeName gemma4
#
# Uses host\qnn_llm_host.exe (rebuilt with {"cmd": "reset"} / "reset": true support, see
# cpp\qnn_llm_host_reset.cpp). -Legacy runs the original prebuilt .\qnn_llm_host.exe instead.
param([string]$PipeName = "gemma4", [int]$SeqLen = 3800, [switch]$Legacy)

. "$PSScriptRoot\setup_env.ps1"
$ModelDir = "$PSScriptRoot\models\gemma4_e2b_sc8380xp_ctxt_4000_ar_l-128"
$Exe = if ($Legacy) { "$PSScriptRoot\qnn_llm_host.exe" } else { "$PSScriptRoot\host\qnn_llm_host.exe" }

& $Exe `
  --model_path "$ModelDir\gemma4_e2b_sc8380xp_ctxt_4000-001.pte" `
  --tokenizer_path "$ModelDir\tokenizer.json" `
  --decoder_model_version gemma4 `
  --pipe_name $PipeName `
  --seq_len $SeqLen `
  --performance_output_path "$env:TEMP\qnn_llm_host_speed.txt"
