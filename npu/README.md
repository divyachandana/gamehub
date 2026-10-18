# npu/ — the ExecuTorch side

Everything the games need to run models on the Snapdragon X Elite's Hexagon NPU through ExecuTorch's
QNN backend. The games start these hosts themselves; the scripts here are for running a model by hand.

| Path | What |
|---|---|
| `host/qnn_llm_host.exe` + `qnn_executorch_backend.dll` | Persistent LLM server on a named pipe (built, git-ignored) |
| `host/src/` | Sources: `qnn_llm_host_reset.cpp` (the host), `qnn_static_host.cpp` (Laya's host), reference runners |
| `patches/` | `executorch-npu-hosts.patch` — everything changed in ExecuTorch `fde20d91` to build the hosts; `executorch-runner-mask-rank.patch` — the attention-mask fix alone |
| `models/<model>/` | Gemma-4-E2B and Qwen3-1.7B: `.pte` + `tokenizer.json` (git-ignored), chat template + tokenizer config |
| `laya/` | Laya: NPU host wrapper, QNN export script, scenarios; `bin/` and the `.pte` are git-ignored |
| `start_host.ps1` | Start Gemma (default) or Qwen3 (`-Model qwen`) on a pipe |
| `tools/pipe_chat_client/chat_client.ps1` | Chat with a running host from a terminal |
| `setup_env.ps1` | QAIRT SDK environment for a terminal (`. .\npu\setup_env.ps1`) |
| `utils/` | Reference copies of ExecuTorch's QNN Python utils |

```powershell
.\npu\start_host.ps1                                          # terminal 1
.\npu\tools\pipe_chat_client\chat_client.ps1 -PipeName gemma4  # terminal 2
```

How to build the hosts, get the models, the pipe protocol and troubleshooting:
[docs/npu-setup.md](../docs/npu-setup.md) · [docs/models.md](../docs/models.md).
