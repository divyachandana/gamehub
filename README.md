# ExecuTorch QNN Playground (Windows-on-Snapdragon)

Dogfooding ground for running and benchmarking ExecuTorch's QNN (Qualcomm AI Engine
Direct) backend natively on a Snapdragon X (SC8380XP) Windows-on-ARM device — no
`adb`, no cross-compiling, this device is both host and target. Full narrative of
every bug and gap found along the way lives in [`dog_fooding_writeup/`](dog_fooding_writeup/).

## Quick start: chat with Gemma-4 over the persistent host

Two terminals. **Terminal 1** starts the model once and keeps it warm; **Terminal 2**
talks to it.

**Terminal 1 — start the host:**
```powershell
cd C:\Users\ADMIN\Desktop\git\x-elite-playground\executorch

$env:QNN_SDK_ROOT = "C:\Qualcomm\AIStack\QAIRT\2.46.0.260424"
$env:Path = "C:\Users\ADMIN\Desktop\git\executorch\build-arm64-windows\backends\qualcomm\RelWithDebInfo;$env:QNN_SDK_ROOT\lib\aarch64-windows-msvc;$env:Path"
$env:ADSP_LIBRARY_PATH = "$env:QNN_SDK_ROOT\lib\hexagon-v73\unsigned"

.\qnn_llm_host.exe `
  --model_path "C:\Users\ADMIN\Desktop\models\gemma4_e2b_sc8380xp_ctxt_4000_ar_l-128\gemma4_e2b_sc8380xp_ctxt_4000-001.pte" `
  --tokenizer_path "C:\Users\ADMIN\Desktop\models\gemma4_e2b_sc8380xp_ctxt_4000_ar_l-128\tokenizer.json" `
  --decoder_model_version gemma4 `
  --pipe_name gemma4 `
  --seq_len 3800
```
`--seq_len` here is the *default* ceiling on the running conversation position for
requests that don't specify their own (see [The persistent host protocol](#the-persistent-host-protocol)
below) — 3800 leaves headroom under this model's compiled 4,000-token context and its
128-token AR chunk (max usable is `context_len − AR` = 3,872). Wait for
`Listening on \\.\pipe\gemma4` — that's the one-time model-load/QNN-context-restore
cost (~2s), paid once for the life of this process, not per request.

**Terminal 2 — chat with it:**
```powershell
cd C:\Users\ADMIN\Desktop\git\x-elite-playground\executorch
.\tools\pipe_chat_client\chat_client.ps1 -PipeName gemma4
```
Type a prompt, press Enter, watch it stream back token-by-token. Type `exit` to
disconnect (leaves the host running) or `shutdown` to stop the host itself.

That's it — no export, no `adb`, no rebuild needed. Swap the two `--model_path`/
`--tokenizer_path` values and `--decoder_model_version` to point at any other
compiled `.pte` under `C:\Users\ADMIN\Desktop\models\`.

## Prerequisites (why those three env vars)

| Var | Why |
|---|---|
| `QNN_SDK_ROOT` | Root of the extracted QAIRT SDK. |
| `Path` (backend dir) | `qnn_executorch_backend.dll` — without it, every exe here fails to even start (fixed to fail *loudly* now, see [Known gotchas](#known-gotchas)). |
| `Path` (`lib\aarch64-windows-msvc`) | `QnnHtp.dll` and friends — the undocumented Windows-ARM64 QNN library directory. |
| `ADSP_LIBRARY_PATH` | Points at the HTP skel `.so` **and** its Windows `.cat` signature file together (Snapdragon X requires both, co-located) — missing this is `DspTransport.openSession qnn_open failed, 0x80000406`. |

Full detail and every error message these produce when missing:
[`.claude/skills/run-model/SKILL.md`](.claude/skills/run-model/SKILL.md).

## What's in this repo

| Path | What |
|---|---|
| `qnn_*.exe` | Prebuilt runners, copied from the main `executorch` build tree — `qnn_llama_runner` (one-shot), `qnn_llm_host` (persistent, named-pipe server), `qnn_multimodal_runner`, `qnn_executor_runner` (simple op tests, e.g. `archive/add.pte`). |
| `cpp/` | Reference copies of the runner source (the real, editable source lives in the main `executorch` checkout at `examples/qualcomm/oss_scripts/llama/`). |
| `tools/pipe_chat_client/` | `chat_client.ps1` — interactive console client for the persistent host's named-pipe protocol. |
| `tools/prefill_sweep/` | `run_sweep.sh` + `gen_prompts.py` — sweeps prefill/decode throughput across prompt lengths for a given `.pte`. `results_*.jsonl` are stored runs. |
| `tools/context_binary_extract/` | `run_extract.sh` — pulls the raw QNN context binary(ies) out of a `.pte` and validates them with `qnn-context-binary-utility`. |
| `data/` | Benchmark results, incl. `qnn_benchmark_results.xlsx` (Qwen3 context-length sweep, Gemma-4 AR-chunk sweep, both with charts). |
| `dog_fooding_writeup/` | The full bring-up narrative: toolchain gaps, real bugs found and fixed, troubleshooting quick-reference. |
| `.claude/skills/` | `setup` (build environment) and `run-model` (running + troubleshooting) — Claude Code skills for this repo. |

## The persistent host protocol

Newline-delimited JSON over `\\.\pipe\<--pipe_name>`. `chat_client.ps1` speaks this
already; the essentials if you're writing your own client:

```jsonc
// request — only "prompt" is required
{"prompt": "...", "system_prompt": "", "seq_len": 128, "temperature": 0.0, "include_metrics": true, "stream": true}

// non-streaming response, or the terminal line of a streaming response
{"ok": true, "text": "...", "done": true, "metrics": {...}}

// streaming: zero or more of these first (no "ok" key — that's how you tell them apart)
{"delta": "..."}

// shutdown
{"cmd": "shutdown"}  ->  {"ok": true}
```

Two behaviors worth knowing before building a real client on this, both inherited
from the underlying `Runner`, not introduced by the host:
1. **Requests share one continuous conversation** — the KV-cache position advances
   across every call on a connection; there's no per-request reset. `seq_len` is a
   running total across the *whole connection*, not a per-turn budget — a request's
   own `seq_len` (if it sends one) always wins over the host's `--seq_len` default,
   so sending one on every request silently overrides whatever you launched the host
   with. `chat_client.ps1` omits the key entirely unless you pass `-SeqLen` explicitly,
   for exactly this reason.
2. **Exceeding the compiled context length is a hard process crash**, not a
   recoverable error — keep your own token count comfortably under the model's
   compiled `max_context_len`.

### Reset-capable host (`host\qnn_llm_host.exe`)

`start_host.ps1` now launches `host\qnn_llm_host.exe`, rebuilt on this laptop from ExecuTorch
`fde20d91` (`C:\dev\executorch`) with its matching `qnn_executorch_backend.dll` next to it
(`-Legacy` runs the original `.\qnn_llm_host.exe`). It fixes behavior 1 above:

```jsonc
{"cmd": "reset"}                        ->  {"ok": true, "reset": true}   // fresh conversation
{"prompt": "...", "reset": true}        // reset, then answer: one-shot requests never accumulate context
```
`runner.reset()` was a no-op stub upstream; the rebuild makes it set the KV position back to 0, which
is safe because every prefill rebuilds the attention mask from that position. Verified: after a reset
Gemma no longer recalls earlier turns, and 40 consecutive `"reset": true` requests ran without a crash.
Source: [`cpp/qnn_llm_host_reset.cpp`](cpp/qnn_llm_host_reset.cpp) (+ `reset()` / `reset_stats()` in
`examples/qualcomm/oss_scripts/llama/runner/runner.h` of that checkout). Used by the snake game in
`C:\Users\qc_de\Desktop\snake_npu`.

Full writeup: [`dog_fooding_writeup/running-qnn-executor-runner-persistent.md`](dog_fooding_writeup/running-qnn-executor-runner-persistent.md).

## Benchmarking a model

```bash
# prefill/decode throughput across a range of prompt lengths
tools/prefill_sweep/run_sweep.sh <model_dir> <pte_filename> <decoder_model_version> <compiled_context_len> 16 128 500 1000 2000

# pull the raw QNN context binary out of a .pte and validate it
tools/context_binary_extract/run_extract.sh <path_to.pte> --validate
```
Both are bash scripts (Git Bash) with defaults matching this device's setup — see
each script's own header comment for the env var overrides.

## Known gotchas

| Symptom | Fix |
|---|---|
| Any `qnn_*.exe` runs and returns instantly with **no output at all** | Missing `qnn_executorch_backend.dll` on `Path` — it's implicitly linked, so the OS loader fails the process before `main()` runs. `qnn_llm_host.exe` now delay-loads it and prints a clear `FATAL: could not load qnn_executorch_backend.dll...` instead. The other runners don't have this fix yet. |
| `DspTransport.openSession qnn_open failed, 0x80000406` | `ADSP_LIBRARY_PATH` not set, or pointed at a folder missing either the skel `.so` or its `.cat` signature file. |
| `Cannot Open QNN library QnnHtp.dll ... LoadLibraryExA failed` | `lib\aarch64-windows-msvc` not on `Path`. |
| `qnn_llm_host` crashes (`F ... assert failed ... sequence length exceeded`) on a **later** turn of a multi-turn pipe conversation, even though `--seq_len` looked generous | A request-level `seq_len` always overrides the host's `--seq_len` default, and it's a whole-connection running ceiling, not per-turn — a client that sends a small `seq_len` on every request (an old default in `chat_client.ps1` sent 256 unconditionally) silently defeats a large `--seq_len` on the host and eventually exceeds it. Fixed in `chat_client.ps1`: it now omits `seq_len` unless you pass `-SeqLen` explicitly. |
| Fluent-looking garbage output, wrong from token 1 | Export commit and runner-build commit disagree on the attention-mask tensor rank — see `dog_fooding_writeup/qnn-backend-windows-arm64.md` §13.6. |
| Model loads, reports healthy tok/s, but output is wrong | Always read the generated text — a healthy-looking run isn't proof of a correct one. |

Full symptom-indexed table: [`dog_fooding_writeup/qnn-backend-windows-arm64.md` §14](dog_fooding_writeup/qnn-backend-windows-arm64.md#14-troubleshooting-quick-reference).
