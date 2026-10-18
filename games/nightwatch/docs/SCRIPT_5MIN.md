# NightWatch · 5-minute demo script

For the recorded video and the live demo. About 560 spoken words (≈4 min of talking); the rest is screen time while
alerts escalate. **SAY** is what you speak, **DO** is what's on screen. *(Brackets)* = optional, cut if running long.

## The six things to land (every section proves one)
| # | Unique point | Where it's proven |
|---|---|---|
| 1 | **Works offline, on the device.** No internet, no cloud, no subscription. ExecuTorch on the laptop. | 2:40: answered run **with Wi-Fi off** |
| 2 | **Keeps escalating until a human responds.** | 1:30: unanswered run |
| 3 | **Earlier, without false alarms.** Leaving the room, not the door; bathroom trips stay quiet. | 1:05 and 3:20 |
| 4 | **Private by design.** Nothing recorded, dots not video, cameras off or night only. | 3:20 |
| 5 | **Reaches everyone at once.** Staff, family, owner; 911 stays a human decision. | 1:30 |
| 6 | **Fits the care workflow.** 2-tap records in their format, morning report, Spanish. | 2:40, 4:30 |

## Before you hit record
- Presenter (`/sim/`) → **Reset everything** → Contacts → **Real sending: ON** (iMessage to the team phone).
- Click the dashboard once (alarm sound). Phone window: name in, **enable sound**.
- Windows: dashboard big on the left, the staff phone window on the right, presenter behind.
- `/camera/`: setup guide done, doorway line drawn, privacy blur on. Unreliable webcam? Use the simulated walk-out and
  say "simulated".
- Know where the Mac's **Wi-Fi off** toggle is (menu bar). Your phone next to the laptop with Messages open.

---

## 0:00–0:35 · The problem

**DO:** Title slide, or the dashboard at night ("All quiet").

**SAY:**
> Mildred Hernandez was a hundred years old and had Alzheimer's. One night in 2019, according to the lawsuit, she
> walked out of her assisted-living facility in Sacramento through a door that locked behind her. She died of
> hypothermia. Her family says staff knew she wandered at night. This year a jury awarded them a hundred and ten
> million dollars.
>
> It isn't rare. Wandering affects roughly a quarter of nursing-home residents with dementia, and the worst cases
> happen at night, when one worker can be covering around forty residents. That's our teammate's experience in
> memory care. A lawsuit comes years later. What she needed was someone at that door within a minute.

## 0:35–1:05 · What NightWatch is (say the promise)

**DO:** Dashboard: floor plan, residents in bed, chips: model **on this laptop**, **Internet needed: No**.

**SAY:**
> This is NightWatch. It notices when a resident leaves their room at night, before they reach a door, and it keeps
> escalating until a human responds. And it runs entirely on this one laptop: no cloud, no subscription, no video
> ever recorded. If the building's internet goes down at 2 a.m., it keeps working.

## 1:05–1:30 · No false alarms (point 3)

**DO:** Presenter → **Visitors come and go** (14:00): visits logged, no alarm. Then **R-15 ensuite trip** (03:00):
gets up, back to bed, nothing fires.

**SAY:**
> During the day, door openings are just logged as visits. At night, R-15 gets up for the bathroom and goes back to
> bed: no alert. False alarms are exactly what teaches staff to ignore alarms.

## 1:30–2:40 · Nobody answers, and it keeps going (points 2 and 5)

**DO:** Presenter → **R-07 heads for the front door** (02:08). Don't touch the phone window. Point at: heads-up →
front door opens → **URGENT** with countdown → **EMERGENCY** takeover → "Who's been contacted" → your phone gets the
iMessages.

**SAY:**
> Two a.m. R-07, who has a history of exit-seeking, leaves their room. The night worker's phone gets a heads-up,
> with what usually calms this resident: a short walk with staff.
>
> The front door opens: urgent. And nobody answers, because the worker is busy with someone else. A normal alarm just
> keeps beeping. NightWatch escalates: every staff phone, the on-call supervisor, the family and the owner, all at
> once. *(Hold up your phone.)* That message just arrived. Emergency services shows "per facility policy": calling
> 911 stays a human decision, and the system refuses emergency numbers by design.

## 2:40–3:20 · The same night, answered, with the internet off (points 1 and 6)

**DO:** **Turn the Mac's Wi-Fi off** on camera. Dashboard is still live (offline badge if shown).
Presenter → **R-07 heads for the front door** again → in the phone window tap **I'm going** at the heads-up →
**Found safe** → the 2-tap care record → **File**.

**SAY:**
> Now I'm turning the Wi-Fi off. Same night, and everything still works: detection, escalation, the staff phone,
> because it all runs on this laptop. This time the worker answers. One tap: I'm going, and the escalation stops.
> Found safe. Then two taps file the care record in the care team's own format, with their pick-lists and their
> severity scale.

**DO:** Turn Wi-Fi back on.

## 3:20–4:00 · Seeing it, privately (points 3 and 4)

**DO:** `/camera/`: walk out across the doorway line (or **Camera: R-07 walks out (simulated)**, and say so).
"LEFT the room" banner → dashboard radar (dots only) → flip the camera **off** ("the webcam is released").

**SAY:**
> How does it know someone left the room? A person detector runs right here, many times a second. You draw the
> doorway once, and only feet crossing it going out count, so staff walking past don't set it off. Every frame is
> analysed in memory and thrown away. The dashboard only ever sees dots, never video. And families and staff decide
> when it watches: night only, or off, and off means the webcam is released.

## 4:00–4:30 · Why on-device matters (point 1, the tech)

**DO:** `/perf/`: live milliseconds per check and per frame, model names, device.

**SAY:**
> Under the hood: two models running through ExecuTorch on the device. A person detector for the camera, and a small
> model that learned what a normal night looks like. The safety-critical escalation is plain rules, so it's always
> predictable. On-device means it works offline, resident data never leaves the building, and watching every
> resident every second costs nothing extra. *(On the Snapdragon laptop: [N] ms per frame on the [NPU/CPU]. Only
> numbers you measured.)*

## 4:30–4:50 · The morning (point 6)

**DO:** `/report/` shift report, then `/family/?id=R-07`. *(Optionally the phone window switched to Español.)*

**SAY:**
> In the morning, the day team gets the handover: every alert, how fast staff responded, records already drafted.
> The family gets a calm summary of their relative's night, with no video and nobody else's data. *(And the staff
> app works in Spanish.)*

## 4:50–5:00 · Close

**DO:** Dashboard, "All quiet". Or the title slide.

**SAY:**
> NightWatch notices earlier, keeps going when nobody answers, and never needs the cloud. Thank you.

---

## If you're running long (cut in this order)
1. The Spanish line (4:30). 2. The family-view sentence (4:30). 3. "A normal alarm just keeps beeping" (1:30).
Never cut the Wi-Fi-off moment: it's the proof of point 1.

## Honesty notes (keep these true on camera)
- Residents are synthetic. If you use the simulated camera walk-out, say "simulated" (it's labelled on screen).
- The phone window on the laptop is "the staff phone view"; a real phone pairs with a PIN over the facility Wi-Fi.
- Offline: detection, escalation and staff phones work without internet. Reaching family and owner (iMessage/push in
  this demo; calls/SMS in production) needs a connection, and when there isn't one the screen says so while staff
  phones are still alerted. Only alert text ever leaves the building.
- No NPU numbers unless measured on the Snapdragon laptop.
- Sacramento: "according to the lawsuit", "her family says", "a jury awarded $110 million" (March 2026; $100M of it
  punitive; against the corporate owner and an investment firm; the defendants may appeal). Wisconsin, if used:
  "according to authorities".
- "Supports staff". It is not a medical device.
