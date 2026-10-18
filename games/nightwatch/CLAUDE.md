# NightWatch: context for Claude

ExecuTorch Hackathon (Oct 17–18 2026, San Francisco), **Compute track**: Copilot+ PC with **Snapdragon X Elite**
(Hexagon NPU). **Code freeze: Oct 18, 11am. Submission: HackerSquad, 1pm, demo video required.**

## What it is
On-device night elopement detection for dementia memory care. A laptop at the nurse station watches hallway cameras
and sensors, and escalates heads-up → urgent → emergency **until a human responds**. Everything runs locally.
Six points every material is built around: offline/on-device; keeps escalating until a human responds; earlier with no
false alarms; private by design; reaches staff + family + owner at once (911 stays human); fits the care workflow.
Read `README.md` first, then `docs/ARCHITECTURE.md`. The 5-minute script is `docs/SCRIPT_5MIN.md`.

## How to run it on this Windows on ARM laptop
```powershell
powershell -ExecutionPolicy Bypass -File setup_windows.ps1   # venv + requirements + health check
python nightwatch\check.py                                    # tells you exactly what works
powershell -ExecutionPolicy Bypass -File demo.ps1             # starts the server, opens the screens
```
If `import executorch` fails with the native ARM64 Python, try an x64 Python (runs under emulation): see
`setup_windows.ps1`. The server itself is standard-library only and falls back to rules if a model can't load.
Pages: `/home/` (start here), `/dashboard/`, `/staff/`, `/sim/` (presenter), `/camera/`, `/perf/`, `/report/`,
`/family/?id=R-07`, `/help/`, `/settings/`, `/story/`, `/wall/`. Tests: `python -m pytest -q tests` (134 pass on macOS).
Full gate before the freeze: `python scripts/qa.py`.

## The open question: the Snapdragon NPU
- **Working:** both models (person detector `models/person_ssdlite_*.pte`, normal-pattern `models/normal_pattern_*.pte`)
  run through ExecuTorch on the CPU (XNNPACK). Verified on a MacBook only so far.
- **Not done:** NPU (QNN) builds. Export needs an x86_64 host with the QNN SDK
  (`nightwatch/vision/export_detector.py --backend qnn --int8`, `nightwatch/model/export.py --backend qnn`), and
  *running* a QNN `.pte` on Windows needs an ExecuTorch runtime built with the Qualcomm backend (the pip wheel lacks it).
  Ask the Qualcomm mentors at the event for help with both.
- **Priority on this laptop:** run it, use its webcam as the hallway camera, and on `/perf/` click "Benchmark the camera
  model" **unplugged** to get real Snapdragon numbers (shows on `/perf/`, results in `logs/bench/`). Put measured numbers
  in the slide placeholders and `docs/SUBMISSION.md`.

## Rules (keep these)
- **Honesty:** residents are synthetic; the `camera_walkout` scenario is simulated and labelled; never invent NPU numbers;
  Sacramento case = "according to the lawsuit", "a jury awarded $110 million" (March 2026); "supports staff", not a
  medical device.
- **Data:** `data/` and `logs/` are git-ignored and must never be committed. Real de-identified care records exist only
  on the original Mac; never ask for them or add them here. Contacts/credentials live in `data/` (`contacts.local.json`,
  `twilio.env`); never print or commit them.
- **Contacts:** emergency services is always simulated and emergency numbers are refused by design; don't change that.
  Real sending is off by default and resets to off on every server start.
- **Code:** keep the server standard-library only; keep the API backward compatible (pages depend on it); run the tests
  before committing; commit specific files (`git add <files>`, never `git add -A`); push to `main`.
- **Don't** add features before the freeze unless asked; fixing what breaks on this laptop comes first.

## Not in this repo (on purpose)
`data/` (local settings, contacts, credentials, care-record summaries), `logs/`, `models/*.pt` training checkpoints
(the `.pte` files the server uses are committed). The slide deck and shot list are private claude.ai artifacts; the
owner shares them from each page's Share menu.
