# NightWatch UX, accessibility and demo-readiness audit

Audited 2026-10-17, ~18:30–19:30, against the live server on `http://localhost:8000` (the shared "nightwatch"
preview) and the working-tree pages at that time. Four other sessions were editing `/staff/`, `/camera/`,
`/dashboard/`, `/home/`, `/settings/` during the audit, so line-level details may have moved; every finding below was
reproduced in the browser (real scenarios, or the page fed a crafted `/api/state` in the browser only) or read
directly from the code path that runs. `/story/` and `/wall/` did not exist yet and are not covered.

Viewports: 1600×1000, 1280×720 (typical projector), 800×1000 (≈ 200 % zoom of 1600), 375×812 (phone).

## Summary

The core story works and looks finished: the level-3 takeover on the nurse station is unmissable, the phone card
puts **I'm going** on the first screen at 375 px, acknowledge → found safe is 2 taps, the care-record sheet is
genuinely 2 picks + File, and "simulated" is labelled honestly almost everywhere. No page scrolls horizontally at
375 px.

What would hurt the demo or a real night, in order:

1. **The second run of a scenario is silent.** Every scenario calls `reset()`, alert ids restart at 1, and both the
   staff phone and the dashboard remember "already beeped for alert 1 up to level 3". README demo step 4 ("Run 3
   again and tap I'm going") gets no vibration and no tone. Same after any server restart.
2. **Pages lie when the server is gone.** Dashboard keeps the emergency, the countdown and a live Acknowledge
   button frozen on screen with only a small "Server not reachable" pill; the phone shows a green
   "Offline · still protecting" badge on top of "Server not reachable".
3. **A failed tap looks like a success.** If `/api/ack` fails, the button stays disabled, nothing says it failed,
   escalation keeps climbing.
4. **The phone blocks on "Your name"** the first time it opens, even mid-emergency, with no skip.
5. **The most urgent thing is not always the most visible.** An acknowledged L3 keeps the takeover and the summary
   pill while an unacknowledged L2 (about to climb) sits below the fold on a 1280×720 projector.

Contrast is good almost everywhere (body text 7.5–16:1); the one systematic failure is **white on the emergency red
`#ff5263` = 3.16:1**.

## First 5 seconds per page (what a judge sees)

| Page | 1600 / 1280 px | 375 px |
|---|---|---|
| `/dashboard/` idle | Moon logo, "Maple Wing (memory care), demo · Nurse station", green "All quiet · 4 of 4 residents in bed…" pill, big clock, chips row (model on CPU, 0.01 ms per check, Internet needed No, PIN). Floor plan with residents in bed. Looks finished. At 1600 the top bar already wraps to 2 rows and the chips to 2 rows (228 px of chrome). | Header + 10 chips take 633 px; the first alert starts at y≈647, i.e. the bottom fifth of the screen. |
| `/dashboard/` L3 | Full-width striped red takeover: siren disc, "Resident R-07 is OUTSIDE via Front entrance. -3 °C", where/temperature/first alert, "What has worked before", big Acknowledge / Found safe. Excellent. Below it the same alert again in the Active alerts card. | Takeover stacks; Acknowledge/Found safe full width. Fine. |
| `/staff/` | First visit: "Your name" modal over everything (even an active EMERGENCY card). Then: "Start shift" (big blue) above the alert card; red band "EMERGENCY · Next call in 5 s", title, Look for, Their room "12 · R-07", What helps, I'm going (blue), Found safe (green), Need backup (orange outline, below the fold). | Same; I'm going at y≈669 of 812. |
| `/sim/` | "Presenter controls", NIGHT + clock + "1 active · highest level 1", nav to every screen, 7-beat demo guide with quote and "Do:" line, scenario cards. Very good presenter tool. | No overflow. |
| `/camera/` | "Hallway camera · On-device person detection · nothing recorded", Setup guide step 1/5, Start camera, blurred-by-default stage. (Being reworked; webcam itself not tested, the pane has no camera.) | No overflow; guide fits. |
| `/report/` | "Night shift handover", tiles, severity, every alert, care records, footer "Synthetic demo residents. Not a medical device". Empty night reads "ALERTS 0 · all closed". | No overflow. |
| `/perf/` | "On-device performance · measured, not estimated", live 0.008 ms per check, detector 5.9 ms/frame, benchmark tables tagged "Dev MacBook (not the demo hardware)". **No NPU row yet.** | Tables scroll inside their panel (1016 px in 305 px). |
| `/home/` | Facility card, 7 screen cards with live chips (ALL CLEAR / LIVE), scenario buttons, pairing PIN, system status. | not measured in detail |
| `/settings/` | Form with a clear warning "Saving restarts the night: current alerts are cleared". All inputs labelled, no target under 24 px. | not measured in detail |

## Findings

Sorted by severity. "Verified" = reproduced in the browser at the stated viewport, or (for "code") read from the code
path that runs and reproduced where the server allowed.

### Blocker

| # | Page | What's wrong | Why it matters | Fix |
|---|---|---|---|---|
| B1 | `/staff/`, `/dashboard/` | **Re-running a scenario (or restarting the server) makes alerts silent.** `seen[a.id]` (staff) and `heard[a.id]` (dashboard) are never cleared; every scenario starts with `e.reset()` which sets `_next_alert = 1`. Verified on `/staff/` with a stubbed state: run 1 L1/L2/L3 → 3 vibrations; reset; run 2 L1/L2/L3 → **0** vibrations, 0 tones. | README demo step 4 reruns the elopement scenario and expects the phone to buzz. In a facility, a server restart makes the next night's first alerts silent. | In both pages, after the beep loop: `const ids = new Set(s.alerts.map(a => String(a.id))); for (const k in seen) if (!ids.has(k)) delete seen[k];` and detect id reuse: keep `ageOf[a.id]`; `if (ageOf[a.id] != null && a.age_s + 2 < ageOf[a.id]) delete seen[a.id]; ageOf[a.id] = a.age_s;` (dashboard: same with `heard`). Better long-term: add `"created": a.created` to `_alert_view` and key by `${a.id}@${a.created}`. |

### High

| # | Page | What's wrong | Why it matters | Fix |
|---|---|---|---|---|
| H1 | `/dashboard/` | **Stale state after the server stops.** `tick()` only writes "Server not reachable" into the summary pill; takeover, countdown ring ("12 sec"), "First alert 30 s ago", clock `02:10:00`, the green "Internet needed No" chip and a clickable Acknowledge all stay. Verified at 800 px with `/api/state` failing. | The projector says everything is live while nothing is being watched. Exactly the "state that lies" a judge might poke at by stopping the server. | On fetch failure: `document.body.classList.add('nw-stale')`, and in CSS `.nw-stale main, .nw-stale .takeover, .nw-stale .sysbar { opacity:.35; pointer-events:none; filter:grayscale(1) }` plus a full-width red banner at the top: "Lost contact with NightWatch at 02:10:00 · alerts are NOT being checked". Remove the class on the next good poll. Mark the clock with `color: var(--nw-l3)`. |
| H2 | `/staff/` | **Green "Offline · still protecting" shown together with "Server not reachable".** `offlineBadge()` is on the phone page; when the phone loses Wi-Fi both appear (verified at 375 px by firing `offline`). The unreachable icon is also green (inherits `.quiet .nw-ico { color: var(--nw-ok) }`). | On a phone, offline means *not* protected: no alerts reach it. Green + "still protecting" is the opposite of the truth. | Don't call `offlineBadge()` on `/staff/` (it is right on the laptop pages only). In the unreachable state use a red card: `<div class="quiet is-down">` with `.quiet.is-down { border-color: var(--nw-l3) } .quiet.is-down .nw-ico { color: var(--nw-l3) }`, title "Not connected: you will NOT get alerts", and `navigator.vibrate([600,200,600])` once when the state flips to unreachable. |
| H3 | `/staff/` (same code on `/dashboard/`) | **Failed acknowledge looks like it worked.** Click handler does `b.disabled = true; await post(...)` with no `try/catch` and no check of `res.ok`. Verified at 375 px with `/api/ack` rejecting: button stays disabled 3 s+, no message, unhandled rejection, no "responding" line, escalation continues. | The worker walks off believing the alert is acknowledged; the system keeps escalating (or worse, they stop looking at the phone). | Wrap: `try { const r = await post(...); if (!r.ok) throw 0; } catch { b.disabled = false; toast("Didn't reach the laptop. Tap again.", 'bad'); navigator.vibrate?.([200,100,200]); }`. Same for `data-res` and `data-backup`, and in dashboard `click` + `keydown` handlers. |
| H4 | `/staff/` | **"Your name" modal blocks the phone, even during an active alert.** `tick()` calls `askName()` whenever `myName` is empty; the dialog has no skip, Escape does nothing (verified), and it covers the EMERGENCY card. First-time flow = type name + Save + I'm going. | Breaks the "≤ 2 taps" goal on any new phone, and is the first thing a judge sees if they open `/staff/` on their own phone. | Never open it while `s.alerts.length`; act as "Night staff (phone)" and ask afterwards (e.g. in the record sheet). Add a "Skip" button (`type="button"` → `$('namebox').hidden = true; myName = ''`) and close on Escape. Or ask only once from the "You: … change" link. |
| H5 | `/staff/` | **A new alert arriving while the care-record sheet is open is hidden behind it.** Verified: after Found safe the sheet opens; a new URGENT alert renders behind the modal (`elementFromPoint` hits the sheet). Only the beep (if shift started) hints at it. | The worker is filling a form while R-21 heads for the garden door. | In `tick()`: if `!$('sheet').hidden` and any alert's id is not in the previous poll (or level rose), call `closeSheet()` (the draft stays in "Records to file") and focus the new card. |
| H6 | `/dashboard/` | **Acknowledged L3 keeps the spotlight; the unacknowledged L2 is below the fold.** Server sorts by `(-level, created)`, the takeover and the summary pill use `alerts[0]`. Verified at 1280×720: summary "3 active · R-07 · staff responding", takeover shows R-07 (Ana responding), the L2 "Garden door opened… next step in 9 s" card starts at y=1123. | The thing that needs a human *now* is invisible on the projector. | Client-side order: `const rank = a => (a.acked_by ? 0 : 10) + a.level; s.alerts.sort((x,y) => rank(y) - rank(x));` before rendering; summary pill text should name the top *unacknowledged* alert ("R-21 · URGENT · nobody responding · 9 s"). Optionally shrink the takeover to a 1-line strip once its L3 is acknowledged. |
| H7 | `/dashboard/` | **One stray key closes an emergency.** `F` resolves `alerts[0]` as "Found safe, returned to room" with no confirm/undo (verified: one `keydown f` → `POST /api/resolve`). `A` acknowledges as "Nurse station". Single-character shortcuts also fail WCAG 2.1.4. | Someone leaning on the nurse-station keyboard ends the escalation for a resident who is still outside. | Require `Shift+F`, or open the same confirm used elsewhere ("Close alert #1 for R-07 as found safe?"), or show a 5 s "Closed · Undo" toast and only POST after it. Add an "Enable single-key shortcuts" toggle in the `?` help. |
| H8 | `/dashboard/` | **Sound is off by default and nothing asks to turn it on.** On first load the button reads "Sound off"; tones only play after the user clicks it (verified, empty localStorage). | The nurse station is silent in an L3 unless someone pre-clicked a small ghost button. In the demo video, the emergency has no sound. | Default `soundPref = true`; when audio is locked show a full-width yellow bar "Click anywhere to enable alarm sound" (the existing once-only `pointerdown` listener already unlocks it). Keep the toggle for muting. |
| H9 | `/staff/` | **No sound/vibration unless "Start shift" is tapped, and nothing reminds you.** `beep()` plays tones only when `shift` is true; `navigator.vibrate` needs a prior tap in Chrome and doesn't exist on iOS Safari. An alert arriving before "Start shift" is silent; after a reload the button is back. "Start shift" is also the biggest, same-blue button above the alert, competing with I'm going. | Silent phone = missed alert. Visual hierarchy puts setup above the emergency. | When an alert is shown and `!shift`, replace the Start-shift button with a red strip inside the card: "Sound is OFF · tap to turn on". Style Start shift as `.nw-btn` (not `--primary`) so I'm going is the only blue button. Persist `shift` in `sessionStorage` and re-arm on the first tap anywhere. |
| H10 | `/dashboard/`, `/staff/` (camera_walkout) | **The "SIMULATED" tag disappears while the alert it caused is still open.** Verified: 20 s into `camera_walkout`, the alert is at L2 ("Resident R-07 left their room at 02:08") and every camera reports `simulated: false`; the alert title/history never says the camera was simulated, and the radar (only place with the tag) is below the fold at 1280×720 (y=858). | A judge sees a camera-detected exit with no "simulated" label anywhere: overclaim. | In the scenario's `_sim_camera_frame` alert path, suffix the title or first history line with "(simulated camera)"; keep `cam["simulated"]` until `reset()`. On the dashboard alert card show the same yellow `.sim-tag` when the alert came from a simulated camera. |

### Medium

| # | Page | What's wrong | Why it matters | Fix |
|---|---|---|---|---|
| M1 | theme / all | **White on `--nw-l3` (#ff5263) is 3.16:1.** Used by `.nw-l3 .nw-badge` (12.5 px, fails 4.5:1), the staff L3 band countdown "Next call in" (14 px, fails), staff L3 band "EMERGENCY" (19 px/850, passes 3:1 only as large text). `--nw-faint` timeline times on the L3 card background are 4.25:1 (12 px, fail). | AA failure on the most important label; washed out on a projector. | Use dark ink on the red: `.nw-l3 .nw-badge, .nw-badge.nw-l3, .nw-l3 .band { color: #120a02; }` (6.2:1), or darken the fill only: `--nw-l3-fill: #c81e33` (white 5.7:1) for solid backgrounds and keep `#ff5263` for text/borders. Timeline time on red cards: `.alert.nw-l3 .nw-timeline time { color: var(--nw-muted) }` (5.7:1). |
| M2 | `/dashboard/`, `/staff/` | **Live regions announce every second.** Measured mutations in 5 s during an L3: takeover `role=alert aria-live=assertive` 14, summary `role=status` 7, `#alerts` polite 7, `#map-desc` polite 7 (rewritten with the same text); staff `#list aria-live=assertive` 5 (countdown text). | A screen-reader user hears "4 s… 3 s…" and the whole card on loop; real alerts get lost. | Remove `aria-live` from `#alerts`, `#map-desc`, `#summary` and the takeover; add one visually hidden `<div id="announce" aria-live="assertive" class="nw-sr-only">` and write to it only when an alert appears or its level/acked state changes ("Level 3 emergency: R-07 outside via front entrance"). Staff: same, and drop `aria-live` from `#list`. Keep the ring's `role=timer` (it is not live by default). |
| M3 | `/settings/` | **Saving settings wipes open alerts, unfiled records and re-enables cameras staff turned off.** `apply_settings()` → `reset()`, which sets every camera `enabled=True, schedule="always"`. The dialog warns about alerts but not about cameras or records. Observed live: another session's save cleared my running scenario ("Settings saved and applied (alerts cleared)"). | Privacy promise ("off means off") silently broken; a mid-incident save erases an emergency. | In `reset()` keep `enabled/schedule` from the previous `self.cameras` when the id still exists. In the settings page, if `/api/state` has open alerts, disable Save with "Close the N open alerts first". Mention unfiled drafts in the confirm text. |
| M4 | `/staff/` | **"Found safe" is one tap with no undo**, while the less dangerous "Need backup" uses tap-to-arm. The button also moves up into I'm going's spot after acknowledging. | One pocket tap ends escalation for someone still outside. | Same arm pattern as backup ("Tap again to confirm found safe", 4 s), or a 5 s "Closed · Undo" bar before POSTing `/api/resolve`. |
| M5 | `/staff/` | **Disconnect is silent.** When polling fails the page swaps to "Server not reachable" with no vibration/tone, and the clock stays at the last value. | Phone in a pocket: the worker doesn't know alerts stopped. | On the first failed poll after a good one: `navigator.vibrate([600,200,600])`, play one low tone if shift started, and show the clock struck through / red. |
| M6 | `/staff/` | **Many alerts: no overview.** 12 alerts = 8,074 px of cards (each ~670 px), no count, no "+N more below". | With 2–3 simultaneous alerts the second one is a full screen down. | Above the list, when `s.alerts.length > 1`: a sticky strip "3 alerts · 1 emergency · 1 urgent · 1 heads-up" with anchors to each card; collapse acknowledged cards to their band + title. |
| M7 | `/dashboard/` | **Active alerts panel doesn't scale.** 12 alerts = 5,273 px column; each card repeats the full escalation history and tips. | On the projector only the first card is visible. | Render cards after the first two in a compact form (badge, title, ring, Acknowledge) with the history in `<details>`. |
| M8 | `/dashboard/` (800 px ≈ 200 % zoom, 375 px) | **Header breaks at 200 % zoom / narrow.** "Sound off", "Shift report", "NIGHT MODE" wrap onto 2 lines inside their pills; at 375 px the 10 chips push the takeover to y≈647. | Looks broken when zoomed; emergency below the fold on small screens. | `.top-right .nw-btn, .nw-mode { white-space: nowrap; }`; at `max-width: 1000px` move `.sysbar` after `main` (`display:flex; flex-direction:column` on body with `order`) or wrap the chips in `<details><summary>System</summary>…</details>`. |
| M9 | all pages | **Navigation is inconsistent; Home and Settings are unreachable.** Dashboard links only Performance + Shift report; sim links Nurse station/Staff/Camera/Performance/Shift report; camera links Nurse station/Staff/Presenter/Performance; perf links Nurse station/Camera/Presenter. Nothing links to `/home/` or `/settings/`; brand text differs (NightWatch / Presenter controls / Hallway camera / On-device performance). | Judges clicking around hit dead ends; the hub page is invisible. | Make the brand mark + "NightWatch" a link to `/home/` on every page (`<a class="nw-brand" href="/home/">`), keep the page name as `.nw-brand-sub`, and give every laptop page the same nav set (Home · Nurse station · Presenter · Camera · Report · Performance · Settings). |
| M10 | `/home/`, `/perf/` | **Efficiency claims ahead of the data.** Home's Performance card says "Measured ExecuTorch latency and energy, NPU vs CPU"; `/perf/` currently shows only CPU rows from "Dev MacBook (not the demo hardware)" with power "n/a*". Pattern model shows `SIZE MB 0.0`, and `OUTPUT DIFF 0.0e+0` with a single CPU row (diff against nothing). | On-device efficiency is 15 % of the score; a judge sees no NPU number. | Run `nightwatch/bench/benchmark.py` (NPU + CPU) on the Snapdragon before recording. Until then, Home text: "Measured ExecuTorch latency on this laptop; NPU vs CPU once benchmarked". Perf: show size in KB when < 1 MB; show "–" for output diff when only one device ran. |
| M11 | `/dashboard/` | **Floor-plan residents are focusable buttons inside `svg role="img"`.** Children of `role=img` are presentational, so screen readers announce one image while Tab lands on invisible "buttons". | Keyboard/AT users can't tell what they are on. | Change `#map` to `role="group" aria-label="Floor plan"`; keep `role="button"` + `aria-label` on each `.res`. |
| M12 | `/dashboard/` vs `/staff/` | **Duplicated L3 at narrow widths.** At ≤1000 px the takeover and the Active alerts card show the same alert back to back with two Acknowledge buttons. | Looks unfinished on a laptop screen at 200 % zoom. | When the takeover is visible, add `.alert[data-id="<takeover id>"] { display:none }` at `max-width:1000px`, or collapse that card to one line "Shown above". |

### Low

| # | Page | What's wrong | Fix |
|---|---|---|---|
| L1 | `/dashboard/` | Summary pill truncates with an ellipsis when the resident/title is long ("…· next step in 9 s" cut off); with a long name the ask-for-action part is lost. | Put the countdown first: `next step in 9 s · 12 active · R-07`, or allow 2 lines (`white-space: normal`). |
| L2 | all | Temperature formatting differs: server alert titles "-3.0 °C", chips/takeover "-3 °C". | Format in `engine.py` with `f"{t:g} °C"`. |
| L3 | all | Mode wording differs: "NIGHT MODE" (dashboard, home), "NIGHT" (staff), "Night" (camera). Page titles: "NightWatch Camera" vs "NightWatch · …". | Pick "NIGHT" + icon everywhere; title `NightWatch · Camera`. |
| L4 | `/staff/` | "Their room: 12 · R-07" (no "Room"). Dashboard says "R-07 · Room 12". | `Room ${a.room} · ${a.resident}` (ES: `Habitación`). |
| L5 | `/report/` | Empty night reads "ALERTS 0 · all closed". | When `t.alerts === 0`, sub-text "none tonight". |
| L6 | `/home/` | Front entrance camera status repeats itself: "On · Always · no frames right now · On, but no frames yet · start it on /camera/". | Use either `camState().sub` or the schedule text, not both. |
| L7 | all except `/report/` | No `<h1>`; page name is a `div.nw-brand-name`. | Make the page name an `h1` with the existing class (`h1.nw-brand-name { margin:0 }`). |
| L8 | `/perf/` | Table scroll containers (1,016 px table in 305 px at 375 px) aren't focusable, so keyboard users can't scroll them. | `<div class="tablewrap" tabindex="0" role="region" aria-label="Benchmark results">`. |
| L9 | `/dashboard/` | Header buttons are 36 px tall, log filter chips 32 px, "Open R-07" 32 px. Fine for a mouse (WCAG 2.2 AA minimum is 24 px) but small for a touch projector. | `.top-right .nw-btn, .nw-chipbtn { min-height: 40px }` if the dashboard is used on a touch screen. |
| L10 | `/dashboard/` | Map colours: "up" (#8ab4ff) vs "in bed" (#7d8fc4) dots are 1.5:1 apart; L2 vs L3 dots 1.4:1. Mitigated: the label says "· bed" and alerting residents get an "L2/L3" tag. | Make in-bed dots hollow (`fill:none; stroke:#7d8fc4`). |

## Task-flow check

| Flow | Result |
|---|---|
| Phone: acknowledge + close an alert | **2 taps** (I'm going → Found safe), then the record sheet opens; "Later" closes it (3rd tap, optional). Filing = 2 picks + File. Blocked on a new phone by H4. |
| Phone: primary action visible at 375×812 | Yes: I'm going at y≈669 on an L3 card; Found safe partly visible; Need backup below the fold. |
| Phone: feedback after acknowledge | Good: "Ana (audit) is responding" replaces the button immediately. Bad when the request fails (H3). |
| Dashboard: acknowledge from the projector | Takeover Acknowledge is 220×64 px at 1600 and full width at 800. Good. |
| Dashboard keyboard | `?` help, `/` search, Esc closes overlays, drawer/help trap focus, focus ring is a 3 px accent outline (9.4:1). Risky single-key A/F (H7). |
| Reduced motion | `prefers-reduced-motion` stops the L3 pulse, takeover stripes and all transitions. Text/icons still carry the level. Good. |
| Horizontal scroll | None on any page at 375 / 800 / 1280 / 1600 (only hidden off-canvas drawer extends past the edge). |

## Quick wins under 10 minutes

1. **B1**: clear `seen`/`heard` entries for ids not in the current poll and on `age_s` going backwards (4 lines in each of staff and dashboard).
2. **H2**: remove `offlineBadge($('offline-host'))` from `/staff/`; make the unreachable card red with "you will NOT get alerts".
3. **H3**: `try/catch` + `res.ok` check around ack/resolve/backup posts; re-enable the button and show an error.
4. **H4**: skip `askName()` while `s.alerts.length > 0`; add a Skip button.
5. **H6**: sort alerts client-side so unacknowledged come first; summary pill names the top unacknowledged one.
6. **H7**: change dashboard shortcuts to `Shift+A` / `Shift+F` (update the `?` help text).
7. **H8**: default dashboard sound on + "Click anywhere to enable alarm sound" bar when audio is locked.
8. **M1**: `.nw-l3 .nw-badge, .nw-badge.nw-l3, .nw-l3 .band { color:#120a02 }` (3.16 → 6.2:1).
9. **M2**: drop `aria-live` from `#alerts`, `#map-desc`, the takeover and staff `#list`; one hidden announcer updated on level changes only.
10. **M8**: `white-space: nowrap` on `.top-right .nw-btn, .nw-mode`.
11. **H1**: on a failed poll, add a body class that greys out the dashboard and shows "Lost contact at HH:MM:SS".
12. **H10**: append "(simulated camera)" to the camera_walkout alert title.

## Demo-day checklist (not code)

- Reload `/staff/` and `/dashboard/` between scenario runs until B1 is fixed, and set the staff name once before recording (H4).
- Click the dashboard's Sound button and the phone's Start shift before recording (H8, H9).
- Run the NPU benchmark on the Snapdragon so `/perf/` shows an NPU row (M10).
- `/home/` currently reports "Facility file EDITED · loaded from facility.local.json": check `data/facility.local.json` is what you want on screen, or delete it to get the stock demo facility.
- Only one person should drive the shared server while recording: during this audit, other sessions' scenario runs and a settings save reset the night several times mid-scenario.
