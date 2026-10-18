# Laya (System 1 decision model) on the Snapdragon X NPU

[Laya](https://huggingface.co/convaiinnovations/laya) (Convai Innovations, Apache-2.0) is a "System 1"
decision model: ModernBERT-large encoder (421M params) + a small decision head. You give it a **state**
(text or JSON) and **typed questions** (`choice` / `score` / `noul` = yes-no probability); it returns
calibrated answers in one forward pass. It does **not** generate text — it is not a chat model.

Converted here: the `typed-decisions` checkpoint → `models\laya_typed_decisions_sc8380xp_s512\` —
QNN HTP **fp16**, SoC **SC8380XP**, fixed 512 tokens, up to 16 options per question. The whole graph is
one QNN delegate (nothing on CPU). NPU answers match PyTorch CPU to within ~0.003 probability;
~300 ms per question on the NPU (+ ~2 s model load per call).

All commands below are run from this folder (`C:\Users\qc_de\Desktop\laya_x_elite`).

## Run

```powershell
.\run_laya.ps1                                                   # model-card example email
.\run_laya.ps1 --state "The app crashes on login"                # your own text (or JSON, or @file.txt)
.\run_laya.ps1 --interactive                                     # type messages, get answers
.\run_laya.ps1 --interactive --questions .\scenarios\it_helpdesk.json   # with another question set
.\run_laya.ps1 --check                                           # also run PyTorch CPU and show both
```

## Question sets (`scenarios\`)

Each file holds `questions` plus test `cases` with expected answers. Edit them in any text editor; use
any of them with `--questions`, or score them with `run_scenario.ps1`.

| File | Input format | Questions |
|---|---|---|
| `my_questions.json` | (questions only) | customer support: department, urgency, churn risk, refund |
| `food_delivery.json` | plain text + one JSON | issue (6 options), anger 0–3, wants refund, safety risk |
| `product_reviews.json` | plain text reviews, incl. sarcasm | sentiment 0–4, main topic (8 options), recommend, defect |
| `it_helpdesk.json` | JSON ticket objects | category (6 options), priority 0–3, needs onsite, security incident |
| `email_phishing.json` | email JSON (from/subject/body/links) | is phishing, tactic (5 options), risk 0–2, asks for money |
| `chat_moderation.json` | chat transcript (list of messages) | toxicity 0–3, rule broken (5 options), needs moderator, on topic |
| `server_logs.json` | multi-line log text | severity 0–3, component (6 options), outage, page on-call |

```powershell
.\run_scenario.ps1 .\scenarios\email_phishing.json            # PASS/MISS per answer + score
.\run_scenario.ps1 .\scenarios\email_phishing.json --probs    # also every option's probability
.\run_scenario.ps1 .\scenarios\email_phishing.json --check    # also PyTorch CPU side by side
.\run_scenario.ps1 all                                        # every scenario file
```
Pass rules: choice = same option; score = within 0.75 of the expected level; yes/no = same side of 0.5.

Results from the first full run, scored against the expected answers I wrote (NPU and CPU agree;
the misses are the model's own calls, not conversion error):

| Scenario | Score |
|---|---|
| food_delivery | 25/32 |
| server_logs | 16/20 |
| product_reviews | 15/20 |
| chat_moderation | 15/20 |
| it_helpdesk | 14/20 |
| email_phishing | 12/20 |

Question file format (same as Laya's API):
```json
{"department": {"type": "choice", "instructions": "Which team?", "criteria": {"billing": "payments", "technical": "bugs"}},
 "urgency":    {"type": "score",  "instructions": "How urgent?", "criteria": ["low", "medium", "high"]},
 "churn_risk": {"type": "noul",   "instructions": "Does the user threaten to leave?"}}
```

## Files

| File | What |
|---|---|
| `setup_env.ps1` | QNN SDK env vars for the NPU runner (dot-source: `. .\setup_env.ps1`). |
| `run_laya.py` / `run_laya.ps1` | Tokenize → `bin\qnn_executor_runner.exe` on NPU → calibrated answers. |
| `run_scenario.py` / `run_scenario.ps1` | Score a scenario file. |
| `laya_model.py` | Export-friendly rebuild of upstream `rl_common.DecisionModel` (static shapes, unrolled head, one-hot instead of gather) + ported tokenization/post-processing. Verified bit-exact vs upstream on CPU. |
| `export_laya_qnn.py` / `export_env.ps1` | `.pte` exporter (`--precision fp16` default; `16a8w` exists but collapsed accuracy with the tiny calibration set — don't use it as-is). |
| `bin\` | `qnn_executor_runner.exe` + `qnn_executorch_backend.dll`, built from the same ExecuTorch commit as the export. |
| `samples.py` | Model-card example + a few extra states (default questions for `run_laya.ps1`). |

## Requirements on this laptop

- QAIRT SDK `C:\Qualcomm\AIStack\QAIRT\2.46.0.260424` (used by `setup_env.ps1`).
- Python `C:\dev\et-venv` (ARM64 Python 3.12, torch 2.14.0+cpu, transformers 5.0.0rc1) — the run scripts use it for tokenization.

## Re-exporting

- ExecuTorch source: `C:\dev\executorch` (commit `fde20d91`), built with
  `backends\qualcomm\scripts\build.ps1 -SkipX86Windows`, plus two local patches:
  1. `backends/qualcomm/CMakeLists.txt` — allow the AOT pybind module on ARM64.
  2. `backends/qualcomm/utils/check_qnn_version.py` — use `lib\aarch64-windows-msvc` on an ARM64 host
     (upstream hard-codes `x86_64-windows-msvc`, so AOT export fails with `Cannot Open QNN library ...x86_64-windows-msvc\QnnHtp.dll`).
  After building, `PyQnnManagerAdaptor.cp312-win_arm64.pyd` was copied into `backends\qualcomm\python\`
  and `schema\*.fbs` into `exir\_serialize\` (the build script only does this for the x86 build).
- Laya weights: `C:\dev\laya_src\typed-decisions`.

```powershell
. .\export_env.ps1
python .\export_laya_qnn.py --model_dir C:\dev\laya_src\typed-decisions --out_dir .\models\laya_typed_decisions_sc8380xp_s512
```
Takes ~3 minutes and ~6 GB RAM.
