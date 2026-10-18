# NightWatch: demo video shot list (≤ 5 min)

The spoken words and order come from [`SCRIPT_5MIN.md`](SCRIPT_5MIN.md); this file is the camera side: what is
on screen, captions, and the setup checks. Each shot proves one of the six points:

1. **Works offline, entirely on the device** (ExecuTorch on the laptop; no cloud, no subscription)
2. **Keeps escalating until a human responds**
3. **Earlier, without false alarms** (leaving the room, not the door; bathroom trips stay quiet)
4. **Private by design** (nothing recorded, dots not video, cameras off or night only)
5. **Reaches staff, family and owner at once** (911 stays a human decision; emergency numbers refused)
6. **Fits the care workflow** (2-tap records in the care team's format, morning report, Spanish)

## Setup before recording

- Laptop with a webcam, from the repo root: `python nightwatch/check.py`, then `./demo.sh` (Snapdragon laptop:
  `powershell -ExecutionPolicy Bypass -File demo.ps1`). The launcher picks the QNN (NPU) models if they're in
  `models/`, otherwise the XNNPACK (CPU) ones. The dashboard chips and `/perf/` name the device; the voiceover
  must match. No NPU claim unless they say NPU.
- Presenter (`/sim/`) → **Reset everything** → Contacts → **Send test**, then **Real sending: ON** (it resets to
  OFF at every start). Contacts are team members who agreed; numbers stay masked on screen.
- Click the dashboard once (alarm sound). Staff phone window: name in, **enable sound**.
- Windows: dashboard big on the left, the staff phone window on the right, presenter behind.
- `/camera/`: setup guide done, doorway line drawn, privacy blur on. If detection on a real person isn't
  reliable, use **Camera: R-07 walks out (simulated)**: it is tagged SIMULATED on screen, and the voiceover says
  "simulated".
- Know where the Mac's Wi-Fi toggle is. A phone next to the laptop with Messages open.

## Shot list

| # | Time | On screen | Proves | Caption |
|---|---|---|---|---|
| 1 | 0:00–0:35 | Title slide, or the dashboard at night ("All quiet"). Voiceover: the Sacramento case. | — | *Residents in this demo are synthetic* |
| 2 | 0:35–1:05 | Dashboard: floor plan, residents in bed, chips: model **on this laptop**, **Internet needed: No**. | 1 | *Runs on this laptop · no cloud* |
| 3 | 1:05–1:30 | Presenter → **Visitors come and go** (14:00): visits logged, no alarm. Then **R-15 ensuite trip** (03:00): up, back to bed, nothing fires. | 3 | *Bathroom trip: no alert* |
| 4 | 1:30–2:40 | Presenter → **R-07 heads for the front door** (02:08). Don't touch the phone window. Heads-up (with what calms R-07) → front door opens → **URGENT** countdown → **EMERGENCY** takeover → "Who's been contacted" → cut to the real phone receiving the iMessage (starts "This is a NightWatch demo."). Emergency services row: "per facility policy". | 2, 5 | *Nobody answers → it keeps going* · *Staff, family, owner at once* |
| 5 | 2:40–3:20 | **Turn the Mac's Wi-Fi off** on camera (show the toggle). Dashboard still live, offline badge. R-07 again → staff phone window: **I'm going** at the heads-up → **Found safe** → the 2-tap care record → **File**. Then Wi-Fi back on. | 1, 6 | *Wi-Fi off: still working* · *Care record in 2 taps* |
| 6 | 3:20–4:00 | `/camera/`: walk out across the doorway line (or the simulated walk-out). "LEFT the room" banner → dashboard radar (dots only) → flip the camera **off** ("the webcam is released"); show the night-only option. | 3, 4 | *Dots, never video* · *Off means off* |
| 7 | 4:00–4:30 | `/perf/`: live ms per check and per frame, model names, device. | 1 | *ExecuTorch · [NPU/CPU] · [__ ms/frame]* |
| 8 | 4:30–4:50 | `/report/` shift report, then `/family/?id=R-07`. Optionally the staff phone switched to Español. | 6 | *Morning handover* |
| 9 | 4:50–5:00 | Dashboard "All quiet", or the title slide. End card: repo URL, team names, "Not a medical device. Supports staff; does not replace them." | — | — |

Never cut shot 5: turning Wi-Fi off during the answered run is the proof of point 1. If running long, cut the
Spanish moment, then the family view.

## What must be true in the video

- **Offline, honestly:** detection, escalation and staff phones work without internet. Reaching family and owner
  (iMessage/push in this demo; calls/SMS in production) needs a connection; when there isn't one, the screen says
  so while staff phones are still alerted. Only alert text ever leaves the building. In shot 5 the staff phone is
  the window on the laptop; a real phone would need the local Wi-Fi (or a hotspot).
- Sacramento: "according to the lawsuit", "her family says", "a jury awarded $110 million" (allegations and a
  verdict; the defendants may appeal). Full wording and sources: `SCRIPT_5MIN.md`, `SUBMISSION.md`.
- Residents are synthetic. Nothing from `data/` on screen. The person on camera agreed; privacy blur stays on.
- Simulated camera data stays visibly tagged SIMULATED and is said out loud.
- 911 is never called; emergency services stay a human decision.
- The device on screen matches the voiceover. No NPU numbers unless measured on the Snapdragon.

## Placeholders to fill

- Shot 7 caption: `[NPU/CPU]`, `[__ ms/frame]` (read from `/perf/` during the take)
- Camera tested on a real person: yes/no (shot 6 live vs SIMULATED)
- Live contact message received: yes/no, channel (shot 4)
- End card: team names `[Name] ×4`
