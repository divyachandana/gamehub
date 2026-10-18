# NPU setup (Snapdragon X Elite, Windows on ARM)

Everything here runs natively on the laptop: no cross-compiling, no `adb` — the laptop is both host
and target. You need the QAIRT SDK, the built NPU hosts in `npu/`, and the model files
([models.md](models.md)). `.\setup.ps1` checks all of it.

## 1. QAIRT SDK

Install Qualcomm's AI Runtime SDK (QAIRT, used here: **2.46.0.260424**) to
`C:\Qualcomm\AIStack\QAIRT\2.46.0.260424`, or pass its folder to `.\setup.ps1 -QnnSdk <path>`. It
can't be redistributed in this repo. The hosts need three things from it, which the games set up
themselves (and `npu\setup_env.ps1` sets for a terminal):

| Variable | Why |
|---|---|
| `QNN_SDK_ROOT` | the SDK root |
| `Path` += `lib\aarch64-windows-msvc` | `QnnHtp.dll` and friends (the Windows-ARM64 library folder) |
| `ADSP_LIBRARY_PATH` = `lib\hexagon-v73\unsigned` | the HTP skel `.so` **and** its `.cat` signature, together (SC8380XP = HTP v73) |

## 2. The NPU hosts (built from ExecuTorch)

Two small programs, built from an ExecuTorch checkout with
[`npu/patches/executorch-npu-hosts.patch`](../npu/patches/executorch-npu-hosts.patch):

| Program | What it does | Source |
|---|---|---|
| `qnn_llm_host.exe` | Loads an LLM `.pte` once, serves streamed generations over a named pipe; `"reset": true` gives each request a fresh context | [`npu/host/src/qnn_llm_host_reset.cpp`](../npu/host/src/qnn_llm_host_reset.cpp) |
| `qnn_static_host.exe` | Loads a fixed-shape `.pte` (Laya) once, answers over stdin/stdout | [`npu/host/src/qnn_static_host.cpp`](../npu/host/src/qnn_static_host.cpp) |

The patch, against ExecuTorch commit **`fde20d91`**, adds both build targets plus:

- `Runner::reset()` — upstream was a no-op; it now sets the KV position back to 0, which is safe
  because every prefill rebuilds the attention mask from that position (verified: 40 consecutive
  reset requests, no crash, no memory of earlier turns).
- The **attention-mask fix** — the runner read the mask's sizes at fixed positions (`[2]`, `[3]`),
  assuming a 4-D mask; the Qwen3 export has a 3-D one, so the host crashed right after prefill. It
  now reads the last two dimensions (also as the small standalone
  [`executorch-runner-mask-rank.patch`](../npu/patches/executorch-runner-mask-rank.patch)).
- Two ARM64 fixes for exporting on the laptop (the AOT module's CPU gate, and the QNN library folder
  in `check_qnn_version.py`).

Build (toolchain: ARM64 Python 3.12, CMake, VS 2022 Build Tools with the ARM64 + Clang components —
see [`.claude/skills/setup/SKILL.md`](../.claude/skills/setup/SKILL.md)):

```powershell
git clone https://github.com/pytorch/executorch.git; cd executorch
git checkout fde20d91; git submodule update --init --recursive
git apply <gamehub>\npu\patches\executorch-npu-hosts.patch
backends\qualcomm\scripts\build.ps1 -SkipX86Windows        # -> build-arm64-windows\
cd build-arm64-windows
$msbuild = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\arm64\MSBuild.exe"
& $msbuild examples\qualcomm\oss_scripts\llama\qnn_llm_host.vcxproj /p:Configuration=RelWithDebInfo /p:Platform=ARM64 /m
& $msbuild examples\qualcomm\executor_runner\qnn_static_host.vcxproj /p:Configuration=RelWithDebInfo /p:Platform=ARM64 /m
```

Then copy into this repo:

| From `build-arm64-windows\` | To |
|---|---|
| `examples\qualcomm\oss_scripts\llama\RelWithDebInfo\qnn_llm_host.exe` | `npu\host\` |
| `backends\qualcomm\RelWithDebInfo\qnn_executorch_backend.dll` | `npu\host\` and `npu\laya\bin\` |
| `examples\qualcomm\executor_runner\RelWithDebInfo\qnn_static_host.exe` | `npu\laya\bin\` |

## 3. Laya's Python

Laya's tokenizer and host code use PyTorch + transformers, so the games run it in a separate Python
(set with `.\setup.ps1 -LayaPython <python.exe>`). On this laptop: `C:\dev\et-venv` — ARM64
Python 3.12 with torch 2.14 (CPU) and transformers 5.0.

## 4. Chat with a model on the NPU (no game)

```powershell
.\npu\start_host.ps1                      # Gemma-4-E2B on \\.\pipe\gemma4   (-Model qwen for Qwen3)
# second terminal:
.\npu\tools\pipe_chat_client\chat_client.ps1 -PipeName gemma4
```

Type a prompt and watch it stream back token by token; `exit` disconnects, `shutdown` stops the
host. Laya on its own: `cd npu\laya; .\run_laya.ps1 --interactive`.

### The host protocol

Newline-delimited JSON on `\\.\pipe\<pipe_name>`:

```jsonc
// request — only "prompt" is required
{"prompt": "...", "system_prompt": "", "seq_len": 400, "temperature": 0.0,
 "reset": true, "stream": true, "include_metrics": true}
// streamed pieces first (no "ok" key)
{"delta": "..."}
// then the final line
{"ok": true, "text": "...", "done": true, "metrics": {"prompt_tokens": 113, "generated_tokens": 17, ...}}
// commands
{"cmd": "reset"}     ->  {"ok": true, "reset": true}
{"cmd": "shutdown"}  ->  {"ok": true}
```

- `seq_len` is a ceiling on the KV position (prompt + reply). Exceeding the model's compiled context
  is a **hard abort**, not an error reply — keep requests well under it.
- Without `"reset": true`, requests continue one conversation and the position keeps growing.

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| A `qnn_*.exe` exits instantly with no output | `qnn_executorch_backend.dll` isn't next to it or on `Path`. `qnn_llm_host` prints a clear `FATAL: could not load qnn_executorch_backend.dll`. |
| `DspTransport.openSession qnn_open failed, 0x80000406` | `ADSP_LIBRARY_PATH` unset, or the folder lacks the skel `.so` or its `.cat` signature. |
| `Cannot Open QNN library QnnHtp.dll` | `QAIRT\...\lib\aarch64-windows-msvc` isn't on `Path`. |
| Host crashes right after prefill, log shows `AR-<full context>` | Mask-rank mismatch between export and runner: build with the patch above. |
| `assert failed ... sequence length exceeded` | A request's `seq_len` (or the conversation) went past the compiled context; use `"reset": true` per request. |
| A car shows **MODEL OFFLINE** | That model's host didn't start: see `games\<game>\logs\qnn_<model>.log` (and `hub\logs\<game>.log`). |
| Fluent but wrong output from the first token | Export and runner from different ExecuTorch commits; rebuild the host from the export's commit. |
| Healthy tokens/s but nonsense moves | Always read the text: a fast answer isn't a correct one (the game's decision feed shows each reply). |
