# NightWatch: HackerSquad submission text

Copy each block into the matching form field. `[Brackets]` are placeholders the team must fill.

---

## Title

NightWatch

## Tagline (one line)

Notices when a resident with dementia leaves their room at night and keeps escalating until a human
responds, entirely on one laptop, with the internet off.

## Track

Compute (Copilot+ PC, Snapdragon X Elite)

## Description

Mildred Hernandez was 100 and had Alzheimer's. According to the lawsuit, in February 2019 she left Greenhaven
Estates, an assisted-living facility in Sacramento, through a door that locked behind her, and died of
hypothermia; her family says staff knew she wandered at night. In March 2026 a Sacramento County jury awarded her
family $110 million against the facility's corporate owner and an investment firm. Wandering affects roughly 20–30%
of nursing-home residents with dementia, and the worst cases happen at night, when, in the experience of the
memory-care professional on our team, one worker can be covering around 40 residents.

NightWatch notices when a resident leaves their room at night, before they reach a door, and keeps escalating until
a human responds. It runs entirely on one Snapdragon X Elite laptop at the nurse station. Six things set it apart:

1. **Works offline, entirely on the device.** Two ExecuTorch models run on the laptop: no cloud, no subscription.
   Detection, escalation and the staff phones (over the local Wi-Fi) keep working with the internet down; the demo
   proves it by turning Wi-Fi off during the answered run. Reaching family and owner needs a connection, and when
   there isn't one the screen says so while staff phones are still alerted.
2. **Keeps escalating until a human responds.** Heads-up → urgent (an exit opens at night, or no answer) →
   emergency (still no answer, or the resident is outside), with a full-screen takeover at the nurse station.
   "I'm going" on the phone stops the climb; "Found safe" closes it.
3. **Earlier, without false alarms.** The trigger is leaving the room, not reaching the door: a hallway camera
   watches the doorway, and only feet crossing it going out count, so staff walking past don't fire. Bathroom
   trips inside the room stay quiet; during the day, doors are logged as visits.
4. **Private by design.** Frames are analysed in memory and dropped: nothing is recorded. The nurse station sees
   dots, never video. Each camera can be night only (21:00–05:00) or off, and off means the webcam is released.
   Every switch is logged with who made it.
5. **Reaches staff, family and owner at once.** At emergency, or when staff press **Call for help**, every staff
   phone, the on-call supervisor, the family and the owner are notified together (iMessage and ntfy push in the
   demo; Twilio calls/SMS supported). Calling 911 stays a human decision: emergency numbers are refused, real
   sending is off until the nurse station turns it on, and only alert text (who, where, when, level) ever leaves
   the building.
6. **Fits the care workflow.** After "Found safe", two taps file a care record in the care team's own format and
   severity scale (1 routine / 2 notable / 3 escalation). The morning shift report is the handover in one page;
   families get a calm "How was their night?" view; the staff phone works in English and Spanish.

Rules for what we know is dangerous, AI for what we have to see: the escalation is deterministic and testable;
the models detect people and judge what's unusual. NightWatch supports staff; it does not replace them. It is
not a medical device.

**Also on the laptop:** the staff phone pairs with a 6-digit PIN (only staff devices see alerts) and has Need
backup; a resident drawer with 30-minute graphs; home page, settings with a floor-plan editor, wall display, story
mode, help center and a privacy-notice template for families (`docs/PRIVACY_NOTICE.md`); display modes (high
contrast, larger text); a performance page that runs the NPU-vs-CPU benchmark; state that survives a restart;
and a QA gate (`scripts/qa.py`, 134 tests).

## How we used ExecuTorch

Two models run on the device through ExecuTorch:

- **Person detector (the heavy, continuous workload).** torchvision SSDlite320-MobileNetV3 with COCO
  weights, exported by `nightwatch/vision/export_detector.py`. The browser page `/camera/` sends webcam
  frames to the local server, which runs the detector and keeps only positions. About 5.5 ms per frame on a
  dev MacBook CPU with XNNPACK during development. A camera running all night is the main reason for the NPU.
  - **Precision:** a tracker gives each person a stable id with motion prediction and hysteresis (0.5
    confidence to start following, kept down to 0.3). Staff draw a **doorway line** on the camera page; only
    feet crossing it *going out* counts (held 2 frames), so people walking along the hallway don't fire, and
    crossing back in clears the heads-up. A zoomed crop around the door and a night brightness boost (capped
    at ×4) help in dark hallways.
  - **8-bit:** quantized with PT2E to int8: 3.9 MB instead of 13.8 MB, same boxes on our checks (box overlap
    0.95–0.99). int8 is the format the Hexagon NPU is built for; on a MacBook CPU it's only slightly faster
    (5.3 vs 5.7 ms).
- **Normal-pattern model.** A ~850-parameter network over 8 live features (time of day, in/out of bed,
  movement, heart rate vs resting, corridor, exit zone, minutes up), trained to tell real normal moments from
  scrambled ones. It scores *combinations* (the corridor is normal at 2 p.m., unusual at 2 a.m.) and needs
  no labeled incidents. `nightwatch/model/train_anomaly.py`, exported by `nightwatch/model/export.py`.
- **Backends.** Both export to **XNNPACK** for the CPU and to **Qualcomm QNN** for the **Hexagon NPU** (the
  QNN compile runs on an x86_64 host with the Qualcomm AI Engine Direct SDK). [Team: state whether the QNN
  builds were run on the NPU. As of writing they have not been.]
- **Runtime.** The dashboard and `/home/` show which models and devices are live and the measured latency;
  `/perf/` times both models live and runs the benchmark, labelling each run with the machine it came from. If a model
  fails to load, the server says so and a rule fallback keeps watching.
- **Why on-device.** Camera frames and health readings of people with dementia must not leave the building
  (only a short alert text goes to the contacts the facility chose); facility internet drops at night; and a
  detector running on every frame all night needs a low-power always-on chip.

**Measured on the Snapdragon X Elite** (see `nightwatch/bench/README.md`):

| Per inference | CPU (XNNPACK) | NPU (QNN) |
|---|---|---|
| Person detector, latency p50 | [__ ms] | [__ ms] |
| Person detector, energy | [__ mJ] | [__ mJ] |
| Normal-pattern model, latency p50 | [__ ms] | [__ ms] |
| Normal-pattern model, energy | [__ mJ] | [__ mJ] |

## Privacy

A privacy-notice template for families is in `docs/PRIVACY_NOTICE.md`.

Camera frames are analysed in memory and dropped: nothing is recorded, stored or sent anywhere, and only a
person's position reaches the alert logic. The camera page shows a blurred privacy view by default, and the
nurse station shows a radar of positions, never video.

**Families and staff decide when it watches.** Each camera can be set to night only (21:00–05:00) or turned
off, from the camera page, the nurse station or the presenter page. Off means physically off: frames are
dropped without being decoded and the webcam is released (its light goes off). Every switch is logged with
who made it and appears in the morning report.

## What's real vs simulated

| Real | Simulated or not yet verified |
|---|---|
| Both ExecuTorch models, their XNNPACK and QNN export paths, on-device inference on CPU | Running on the NPU: [not yet run / run on (date)] |
| Person detector on webcam frames, tracker, doorway-line crossing, privacy view, camera on/off/night-only | Detection on a real person: [verified / not yet verified] |
| | The presenter scenario "Camera: R-07 walks out" plays **simulated** camera data; it is tagged SIMULATED on screen |
| The escalation ladder, day/night modes, acknowledgement and resolution | "Normal" for the small model was learned from **simulated days**; in a facility it would come from each resident's own first weeks |
| Staff phone page and nurse-station dashboard over the local network | Residents are **synthetic** (`nightwatch/demo/facility.json`); every number there is invented |
| Works with Wi-Fi off (alerts, models, staff phones on a local network) | Emergency services: always simulated. NightWatch never calls 911 |
| Contact fan-out to on-call, family and owner via iMessage and ntfy push, when real sending is switched on | Twilio calls/SMS: supported, not used in the demo. Real sending is off (dry run) by default; [Team: live message tested on the day: yes/no, channel] |
| Phone pairing PIN, shift report, family view, settings, 134-test QA gate | |
| Bridges for a real door sensor (Arduino + reed switch) and a Bluetooth heart-rate watch | Other sensor events in the demo come from the browser simulator (`/sim/`). [Team: real sensors used on the day, if any] |
| Severity badge and draft care record in the care team's format | |

The design was informed by de-identified care records shared by our memory-care teammate. Those records stay
off the repo (git-ignored); only aggregate profiles were built from them, locally. No real patient data is in
the code, the demo or this submission.

## Setup

Python 3.10+. The models (`.pte`) are committed, so no training is needed.

```bash
git clone https://github.com/lucasmasen/ExecuTorch && cd ExecuTorch
pip install -r requirements.txt     # Windows on ARM: powershell -ExecutionPolicy Bypass -File setup_windows.ps1
./demo.sh                           # Snapdragon laptop: powershell -ExecutionPolicy Bypass -File demo.ps1
```

The launcher picks the NPU (QNN) models if they're in `models/`, otherwise the CPU (XNNPACK) ones, starts the
server on the local network and opens the screens. Start at <http://localhost:8000/> (home, with the phone
PIN). A 2-minute judge tour is in `docs/JUDGES.md`. Open <http://localhost:8000/dashboard/> (nurse station), <http://localhost:8000/staff/> (staff phone),
<http://localhost:8000/sim/> (presenter page and sensor simulator), <http://localhost:8000/camera/> (hallway
camera) and <http://localhost:8000/report/> (morning shift report). `python nightwatch/check.py` checks the
laptop and prints the start command. Add
`--lan` to open `/staff/` from a phone on the same Wi-Fi.

NPU (Snapdragon X Elite): compile on an x86_64 host with the Qualcomm AI Engine Direct SDK and ExecuTorch's
Qualcomm backend, then run on the laptop:

```bash
cd nightwatch/model && python export.py --backend qnn && cd ../..
python nightwatch/vision/export_detector.py --backend qnn
python nightwatch/server.py --model models/normal_pattern_qnn.pte --device NPU \
  --camera-model models/person_ssdlite_qnn.pte --camera-device NPU
```

Demo: follow the guide on the presenter page. For the live beat: press **Reset**, set the clock to **02:08**,
start the camera on *Hallway camera outside room 12*, draw the doorway line and walk out across it: the phone
gets a heads-up. Then run
*Night: R-07 heads for the front door* and leave the phone alone to watch it escalate; run it again and tap
**I'm going**.

## Links

- Repo: https://github.com/lucasmasen/ExecuTorch
- Demo video: [link]
- Slides: [link]

## Team

| Name | Role |
|---|---|
| [Name] | [role] |
| [Name] | [role] |
| [Name] | [role] |
| [Name] | Memory-care professional: problem, workflow and care-record design |

## Sources for the cases we cite

Lawsuit claims are allegations; amounts are jury verdicts as reported. Full links are in the README → Sources.

- Sacramento (Hernandez v. Greenhaven Estates owners): according to the lawsuit, Mildred Hernandez, 100, with
  Alzheimer's, left the facility in February 2019 through a door that locked behind her and died of
  hypothermia; her family says staff knew she wandered at night. In March 2026 a Sacramento County jury awarded
  $110 million ($10.2M compensatory + $100M punitive) against the corporate owner (Colony Capital) and an
  investment firm (Formation Capital). These are allegations and a jury verdict; the defendants may appeal.
  Sources: https://dudensinglaw.com/results/hernandez-verdict/ ·
  https://www.expertinstitute.com/resources/insights/110m-verdict-assisted-living-elopement/ ·
  https://www.yahoo.com/news/articles/family-awarded-110m-following-death-190724321.html
- Wisconsin, about $4M jury verdict; door alarm ignored at 12:41 a.m., according to authorities: McKnight's
  Senior Living.
- Wandering affects roughly 20–30% of nursing-home residents with dementia, depending on study and setting.

## Placeholders to fill

- Benchmark table: detector and normal-pattern model, CPU vs NPU, latency and energy
- Whether the QNN builds were run on the NPU (and when)
- Whether detection was verified on a real person
- Real sensors used on the day (if any)
- Whether a live contact message (iMessage / ntfy) was tested on the day
- Demo video link, slides link
- Team names and roles (×4)
