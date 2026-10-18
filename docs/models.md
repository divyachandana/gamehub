# Models

Three models run on the Snapdragon X Elite's Hexagon NPU, all as ExecuTorch programs (`.pte`)
lowered to Qualcomm's QNN HTP backend for the **SC8380XP** SoC (HTP v73).

| Model | Role in the games | Size on disk | Context | Host |
|---|---|---|---|---|
| **Gemma-4-E2B** (Google) | Racer — reads the junction, replies `{"move", "why"}` | 2.8 GB `.pte` + 31 MB tokenizer | 4,000 tokens (128-token prefill chunk) | `qnn_llm_host.exe`, pipe `rally_gemma` |
| **Qwen3-1.7B** (Alibaba) | Racer, and the rogue in UNFAIR mode; prompted with `/no_think` | 1.7 GB `.pte` + 11 MB tokenizer | 8,000 tokens | `qnn_llm_host.exe`, pipe `rally_qwen` |
| **Laya** typed-decisions (Convai Innovations; ModernBERT-large encoder + decision head, 421M params) | The rogue's instinct: yes/no *cheat now?* with a calibrated probability | 0.8 GB `.pte` (fp16) | 512 tokens, fixed shape | `qnn_static_host.exe` via `laya_bridge.py` |

## Speed on this laptop (measured by the game, per decision)

| Model | Alone on the NPU | Sharing the NPU in a race |
|---|---|---|
| Gemma-4-E2B | ~165 ms to first token · ~13.7 tokens/s · prefill ~500–700 tokens/s | ~8–13 tokens/s |
| Qwen3-1.7B | ~170 ms to first token · ~12 tokens/s | ~8–12 tokens/s |
| Laya | ~0.3 s per question | ~0.3–0.6 s |

Model load (first start): Gemma ~2.7 s, Qwen3 ~2.1 s, Laya ~5 s. Switching games reuses the loaded
LLMs.

## Files and where they go

The `.pte` files and the built runtime are **not in this repo** (git-ignored; 5+ GB). Put them here
— `setup.ps1` checks each one and tells you what is missing:

```
npu/
├── host/
│   ├── qnn_llm_host.exe                 built from npu/host/src + npu/patches (docs/npu-setup.md)
│   └── qnn_executorch_backend.dll       built with it (same ExecuTorch commit)
├── models/
│   ├── gemma4_e2b_sc8380xp_ctxt_4000_ar_l-128/
│   │   ├── gemma4_e2b_sc8380xp_ctxt_4000-001.pte    ← download
│   │   ├── tokenizer.json                            ← download
│   │   ├── tokenizer_config.json, chat_template.jinja   (in the repo)
│   └── sc8380_qwen3-1_7b_ctxt-8000/
│       ├── sc8380_qwen3-1_7b_ctxt-8000.pte          ← download
│       ├── tokenizer.json                            ← download
│       └── tokenizer_config.json, chat_template.jinja   (in the repo)
└── laya/
    ├── bin/qnn_static_host.exe, qnn_executorch_backend.dll    built (docs/npu-setup.md)
    └── models/laya_typed_decisions_sc8380xp_s512/
        ├── laya_typed_decisions_sc8380xp_s512_fp16.pte       ← download or export
        └── rl_agent_config.json, tokenizer/                  (in the repo)
```

### Getting the files

- **Gemma-4-E2B and Qwen3-1.7B `.pte` + `tokenizer.json`:** the team's pre-exported files for
  SC8380XP — download them from: **_add the team's download link here_**. To export your own, use
  ExecuTorch's Qualcomm LLM export (`examples/qualcomm/oss_scripts/llama/` in the
  [ExecuTorch repo](https://github.com/pytorch/executorch)), targeting the SC8380XP SoC, **on an
  x86_64 machine** (the LLM export pulls in `pyarrow`, which has no Windows-ARM64 build); the folder
  names above encode the context length (4000 / 8000) and Gemma's 128-token prefill chunk. Export
  with the same ExecuTorch commit the host is built from (`fde20d91`), or the I/O may not match.
- **Laya `.pte`:** download it with the others, or export it from the
  [Laya checkpoint](https://huggingface.co/convaiinnovations/laya) on this laptop (~3 min, ~6 GB RAM):

  ```powershell
  cd npu\laya
  . .\export_env.ps1      # ExecuTorch source tree + QAIRT SDK on the path
  python .\export_laya_qnn.py --model_dir <laya typed-decisions snapshot> --out_dir .\models\laya_typed_decisions_sc8380xp_s512
  ```
  `fp16` is the default and the one used; `16a8w` exists but lost accuracy with the small
  calibration set. See [npu/laya/README.md](../npu/laya/README.md).

## Why these models

- **Small enough to answer in about a second** on the NPU, so the race is about inference speed,
  not waiting.
- **Two different LLM families** (Gemma, Qwen) make DEMO a real comparison of decision quality and
  speed on the same hardware.
- **Laya isn't a chat model:** one forward pass returns calibrated probabilities for a typed
  question, ~0.3 s on the NPU — a fast "System 1" gut call for the rogue, next to the slower LLM
  "System 2" driver.

## Licenses

Gemma: [Gemma Terms of Use](https://ai.google.dev/gemma/terms) · Qwen3: Apache-2.0 · Laya: Apache-2.0.
