# Struggle Detector

**An on-device helper that notices when a patient is stuck on a health website, and helps before they give up.**
ExecuTorch Hackathon 2026, Compute track (Copilot+ PC, Snapdragon X Elite).

Patients increasingly *have* to use online portals to refill prescriptions, book follow-ups and pay bills.
When they get stuck, they give up, wait for family, or call for help, and nobody notices until it's too late.
Struggle Detector watches *how* someone uses the page (frantic clicks, clicks that do nothing, retyping,
error messages, going back and forth, long pauses) plus heart rate from their watch, and a small model
scores frustration every second. When the score stays high, it offers calm, specific help and highlights the
right field.

It sees everything you do on the page, so **it has to run on the device**: the model runs through ExecuTorch on
the laptop's Hexagon NPU, and no click, keystroke or heart-rate reading leaves the machine.

## How it works

```
portal/ (browser)                     server/app.py (localhost only, Python stdlib)
  tracker.js: 10 behavior features ─►  scorer: ExecuTorch .pte (NPU or CPU) or rule fallback
  once per second (counts only,        + heart rate vs. the user's resting baseline
  never what was typed)                trigger: score >= 0.7 for 3 s, at most once per 15 s
                                    ◄─  help: written message matched to the field / error
dashboard/: live score, heart rate, model + device + ms per score, help ON vs OFF results
server/hr_bridge.py: watch heart rate (Bluetooth) -> server
model/: train.py (features -> tiny MLP), export.py (-> .pte for XNNPACK or QNN)
```

The feature contract lives in `server/features.py` and is mirrored in `portal/tracker.js`.

## Run it

Python 3.10+. The server needs **no packages** unless you load a model.

```bash
python server/app.py                       # rule-based fallback, no model
```

Open <http://localhost:8000/portal/> and <http://localhost:8000/dashboard/> side by side.
Untick "help ON" on the start screen to run the comparison condition. Press **F8** while frustrated to record a label.

With the model:

```bash
pip install torch executorch
python model/train.py --csv data/patients.csv --logs logs/sessions   # see Data below
python model/export.py --backend xnnpack                              # CPU .pte, any OS
python server/app.py --model models/frustration_xnnpack.pte --device CPU
```

NPU (Snapdragon X Elite): compile on an x86_64 host with the QNN SDK and ExecuTorch's Qualcomm backend
(it can't compile on a Mac or natively on Windows on ARM), then copy the `.pte` over:

```bash
python model/export.py --backend qnn
python server/app.py --model models/frustration_qnn.pte --device NPU
```

If a model fails to load, the server says so and falls back to rules; the dashboard always shows which scorer is live.

Heart rate:

```bash
pip install bleak
python server/hr_bridge.py --scan              # find the watch
python server/hr_bridge.py --ble "Charge 6"    # any device broadcasting the standard heart-rate service
python server/hr_bridge.py --simulate          # testing only; labeled "simulated" on the dashboard
```

## Data

- `data/` and `logs/` are git-ignored. **Never commit patient data.** It stays on this laptop.
- `model/train.py --csv` expects the 10 columns in `server/features.py` plus `label` (0/1).
  Map the dataset's columns to these first.
- `--logs logs/sessions` turns our own portal sessions into training rows (F8 = frustrated).
- `--synthetic N` exists only to test the pipeline. Never report its accuracy as a result.

## Status

| Piece | State |
|---|---|
| Portal, tracker, server, dashboard, help, rule fallback | Working (tested on macOS) |
| Train + export to XNNPACK `.pte`, served by the ExecuTorch runtime | Working on macOS with **synthetic** data |
| Model trained on the real patient dataset | To do: map columns to `features.py` |
| QNN `.pte` on the Hexagon NPU | To do: compile on x86_64 with the QNN SDK, run on the laptop |
| Live watch heart rate | To do: needs a watch/app that broadcasts the standard heart-rate service |
| Windows on ARM run | To do: server is stdlib-only, so it should run as-is |

Built on the ExecuTorch runtime. Not a medical device: it helps people use websites; it does not diagnose anything.
