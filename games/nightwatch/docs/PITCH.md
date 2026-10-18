# NightWatch: 3-minute pitch

The short version of [`SCRIPT_5MIN.md`](SCRIPT_5MIN.md) (the reference for order and wording), for a live
3-minute slot. About 430 spoken words; timings are cumulative. Slide names refer to the deck. Placeholders in
`[brackets]` must be filled from the team's own measurements; don't round them and don't say a number we didn't
measure. Until NPU numbers are in `nightwatch/bench/README.md`, say "built for the NPU", never "runs on the NPU".

## The six things to land

| # | Point | Proven at |
|---|---|---|
| 1 | **Works offline, entirely on the device.** ExecuTorch on the laptop; no cloud, no subscription. | 1:40, the answered run with Wi-Fi off |
| 2 | **Keeps escalating until a human responds.** | 1:05, the unanswered run |
| 3 | **Earlier, without false alarms.** Leaving the room, not the door; bathroom trips stay quiet. | 0:50 and 2:10 |
| 4 | **Private by design.** Nothing recorded, dots not video, cameras off or night only. | 2:10 |
| 5 | **Reaches staff, family and owner at once.** 911 stays a human decision; emergency numbers are refused. | 1:05 |
| 6 | **Fits the care workflow.** 2-tap records in the care team's format, morning report, Spanish. | 1:40, 2:45 |

---

## 0:00–0:30 · The problem (slides: Cover, Cases, Problem)

> Mildred Hernandez was a hundred years old and had Alzheimer's. One night in 2019, according to the lawsuit, she
> walked out of her assisted-living facility in Sacramento through a door that locked behind her. She died of
> hypothermia. Her family says staff knew she wandered at night. This year a jury awarded them a hundred and ten
> million dollars.
>
> It isn't rare, and the worst cases happen at night, when one worker can be covering around forty residents.
> That's our teammate's experience in memory care. What she needed was someone at that door within a minute.

*Notes:* "according to the lawsuit", "her family says", "a jury awarded $110 million" (Sacramento County, March
2026; $10.2M compensatory + $100M punitive, against the corporate owner and an investment firm; the defendants
may appeal). Say $110 million. The 1-to-40 ratio is our teammate's experience; say so. Wisconsin, only if asked:
"according to authorities, a door alarm went off at 12:41 a.m. and workers ignored it."

## 0:30–0:50 · The promise (slides: Ladder, Six things)

> This is NightWatch. It notices when a resident leaves their room at night, before they reach a door, and it
> keeps escalating until a human responds. It runs entirely on this one laptop: no cloud, no subscription, no
> video ever recorded.

## 0:50–1:05 · No false alarms (point 3; slide: Demo)

**DO:** presenter → **R-15 ensuite trip** (03:00).

> At night, R-15 gets up for the bathroom and goes back to bed: no alert. False alarms are what teach staff to
> ignore alarms.

## 1:05–1:40 · Nobody answers, and it keeps going (points 2 and 5)

**DO:** presenter → **R-07 heads for the front door** (02:08). Don't touch the phone window.

> Two a.m. R-07 leaves their room: a heads-up on the night phone. The front door opens: urgent. Nobody answers,
> because the worker is with someone else. So NightWatch escalates: every staff phone, the on-call supervisor,
> the family and the owner, all at once. *(Hold up the phone.)* That message just arrived. Calling 911 stays a
> human decision; the system refuses emergency numbers by design.

*Notes:* only show a received message if it really arrived (`[live contact message tested: yes/no, channel]`).

## 1:40–2:10 · Same night, answered, with the internet off (points 1 and 6)

**DO:** turn the Mac's Wi-Fi off on camera, run R-07 again, tap **I'm going**, **Found safe**, the 2-tap record.

> Now I turn the Wi-Fi off. Detection, escalation, the staff phone: everything still works, because it all runs
> on this laptop. This time the worker answers. "I'm going," and the escalation stops. "Found safe," and two taps
> file the care record in the care team's own format.

*Notes:* the phone window on the laptop is the staff phone view; a real phone needs the local Wi-Fi. Offline,
reaching family and owner needs a connection: the screen says so while staff phones are still alerted. Turn
Wi-Fi back on after this beat.

## 2:10–2:30 · Seeing it, privately (points 3 and 4; slide: Why on the device)

**DO:** walk out across the doorway line on `/camera/` (or **Camera: R-07 walks out (simulated)**, and say
"simulated"), then flip the camera off.

> A person detector runs right here. You draw the doorway once, and only feet crossing it going out count. Every
> frame is analysed in memory and thrown away; the dashboard only sees dots. And families and staff decide when it
> watches: night only, or off, and off means the webcam is released.

*Notes:* live walk-out only if `[camera tested on a real person: yes/no]` is yes.

## 2:30–2:45 · The tech (slides: How it works, Results)

> Two models run through ExecuTorch on the device: the person detector, quantized to 8 bits for the Snapdragon's
> NPU, and a small model that learned what a normal night looks like. The safety-critical escalation is plain
> rules, so it's always predictable. On the Snapdragon laptop the detector takes **[detector p50, ms]** per frame
> on the **[NPU/CPU]**.

*Notes:* the 5.3 ms (int8) figure is from a dev MacBook CPU; say "a laptop CPU" if you use it. "Normal" was
learned from simulated days.

## 2:45–3:00 · The morning, and close (slides: Workflow, What's next)

> In the morning, the day team gets the handover, and the family gets a calm summary of the night, with no video.
> The staff app works in Spanish too. NightWatch notices earlier, keeps going when nobody answers, and never needs
> the cloud. Thank you.

---

## Placeholders to fill

- `[live contact message tested: yes/no, channel]`
- `[camera tested on a real person: yes/no]` (live walk-out vs the SIMULATED presenter scenario)
- `[detector p50, ms]` and `[NPU/CPU]`, measured on the Snapdragon X Elite
