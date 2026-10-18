---
name: run-model
description: Run a QNN .pte model on this Windows-on-Snapdragon device — simple op tests via qnn_executor_runner, or LLMs via qnn_llama_runner. Use when validating a model, benchmarking tok/s, or debugging a runner failure.
---

# Running a QNN model (Windows-on-Snapdragon, native ARM64)

Full narrative + symptom-indexed troubleshooting table for everything hit so far: `dog_fooding_writeup/qnn-backend-windows-arm64.md` §14, plus the runner-specific writeups in the same folder.

## Required environment (every runner needs all three)

```powershell
$env:QNN_SDK_ROOT = "<path to extracted QAIRT SDK>"          # e.g. C:\Qualcomm\AIStack\QAIRT\2.49.0.260730
$env:Path = "$env:QNN_SDK_ROOT\lib\aarch64-windows-msvc;<repo>\build-arm64-windows\lib\executorch\backends\qualcomm;$env:Path"
$env:ADSP_LIBRARY_PATH = "$env:QNN_SDK_ROOT\lib\hexagon-v<N>\unsigned"   # N = HTP arch, e.g. 73 for SC8380XP
```
`lib/aarch64-windows-msvc` (undocumented in-repo) is where `QnnHtp.dll` etc. live. `ADSP_LIBRARY_PATH` points at the HTP skel + its Windows `.cat` signature file — **both must be set**, or HTP init fails with `DspTransport.openSession qnn_open failed, 0x80000406`. Look up the HTP arch for your SoC in `backends/qualcomm/serialization/qc_schema.py`'s `SocInfo` table.

## Simple op model — `qnn_executor_runner`

```powershell
cd build-arm64-windows\examples\qualcomm\executor_runner\RelWithDebInfo
.\qnn_executor_runner.exe --model_path add.pte
```
Success looks like `Model executed successfully.` — **ignore a nonzero `$LASTEXITCODE` after that line**; it's a known post-inference cleanup crash on this platform, not a real failure. Also don't pipe `2>&1` in PowerShell 5.1 — INFO-level logs go to stderr and get wrapped as fake `NativeCommandError`s.

## LLM model — `qnn_llama_runner`

```powershell
cd build-arm64-windows\examples\qualcomm\oss_scripts\llama\RelWithDebInfo
.\qnn_llama_runner.exe `
  --decoder_model_version <gemma4|llama3|qwen3|...> `   # must match how the model was exported
  --model_path "<model dir>\model.pte" `
  --tokenizer_path "<model dir>\tokenizer.json" `
  --prompt "What is the capital of France?" `
  --seq_len 128 `
  --eval_mode 1
```
`decoder_model_version` drives prompt templating (`qnn_llama_runner.cpp`'s `get_formatted_prompt`) — wrong value produces coherent-looking but wrong output, not an error. Do **not** pass `--shared_buffer` unless you've confirmed it doesn't degrade prefill quality for this model — it has on at least one model tested here.

Read the log's `Rate:` / `PyTorchObserver {...}` lines for prefill/decode tok/s; `--performance_output_path` writes the blended overall rate to a file for scripted comparison.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Backend QnnBackend is not registered` | `qnn_llama_runner`/`qnn_multimodal_runner` binary predates the `QnnExecuTorchBackendRegister()` fix (MSVC gives the DLL and exe separate globals, so static self-registration never crosses the boundary) | Rebuild from a repo with the fix applied (`main()` must call `QnnExecuTorchBackendRegister(...)` before anything else) |
| `DspTransport.openSession qnn_open failed, 0x80000406` | `ADSP_LIBRARY_PATH` not set, or set to a folder missing the HTP skel `.so` **and** its `.cat` signature file together | Set `ADSP_LIBRARY_PATH` to `$QNN_SDK_ROOT\lib\hexagon-v<N>\unsigned` — that folder already ships both files side by side |
| `Cannot Open QNN library QnnHtp.dll ... LoadLibraryExA failed` | `lib\aarch64-windows-msvc` not on `Path` | Prepend it (see Required environment above) |
| `Using newer context binary on old SDK` / `err 5000` | `QNN_SDK_ROOT` version is older than what the `.pte` was compiled against | Install/point at the matching (or newer) QAIRT SDK version |
| `QnnContextCustomProtocol expected magic number: ... but get: ...` (INFO, not ERROR) | Benign for AI-Hub-style pre-generated `.pte`s — the context blob isn't wrapped in ExecuTorch's own header; the runtime already falls back to reading it raw | Ignore unless immediately followed by a real arch-mismatch error |
| Fluent-looking garbage output, wrong from token 1; `max_context_len` absurdly large in the log; prefill `AR-<full context len>` | Export commit and runner-build commit disagree on the attention-mask tensor's rank (3-D vs 4-D) — a breaking `.pte` I/O contract change with no version guard | Match export/runner commits, or use a runner that indexes the mask shape from the end (`rank-2`/`rank-1`) rather than fixed positions |
| Model loads, runs, reports healthy tok/s — but output is wrong | Looking healthy in the logs proves nothing; every failure above still reported plausible numbers | Always read the actual generated text before trusting a run |
