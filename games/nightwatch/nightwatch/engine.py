"""NightWatch engine: resident state, day/night modes and the escalation ladder.

Level 1  Heads-up   resident up and unusual for this time   -> quiet alert to the night staff phone
Level 2  Urgent     exit door / window opened at night       -> loud alert, must be acknowledged
Level 3  Emergency  no acknowledgement in time, or resident  -> on-call supervisor, then emergency
                    confirmed outside                           services per facility policy

Escalation only climbs when nobody responds: one night worker for a whole wing is often busy
with another resident, so the system keeps going for them. It never dials anyone in this build:
contact steps are logged as "(simulated)".

Restart-safe: with start_autosave(), every change is written (debounced ~1 s, atomically: tmp file +
rename) to logs/night_state.json, and restore() brings back tonight's alerts, events, cameras and the
clock if the server is restarted mid-night. Every change also bumps `rev`, which /api/events pushes.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from features import vector
from notify import FANOUT_GAP_S, MAX_REAL_PER_FANOUT, PREFIX, Notifier, channel_parts, mask_any, recipient

NIGHT_START, NIGHT_END = 21, 5   # default night band, matches the care records (21:00-05:00); configurable
HISTORY_EVERY_S = 5              # resident history sample period (360 samples = 30 min)

# The care team's own pick-lists (taxonomy v2), so confirmed records drop straight into their format.
TAXONOMY: dict[str, list[str]] = {
    "affect": ["Calm", "Unsettled-agitated", "Distressed-tearful", "Drowsy", "Withdrawn", "Confused-disoriented",
               "Content-engaged"],
    "intervention": ["Reassurance", "Reorientation", "Music/sensory", "Repositioning", "Presence / 1-to-1",
                     "Direct care provided", "Clinical review"],
    "nondrug_alt": ["Music", "Movement", "Sensory", "Cognitive", "Family involvement", "Diet/hydration"],
}
SEVERITY = {1: "routine", 2: "notable", 3: "escalation"}
CAMERA_CONFIRM_FRAMES = 3        # consecutive frames with a person before we believe it (~0.75 s)  # the care team's derived severity scale
UNUSUAL_ALERT = 0.9              # model score that counts as "unusual for right now"
SUSTAIN_S = 5                    # ...for this many consecutive seconds (one odd reading is not an alert)
STATE_VERSION = 1                # logs/night_state.json format
SAME_NIGHT_GRACE_S = 2 * 3600    # a state file this fresh is always restored (a restart mid-demo)
MAX_STATE_AGE_S = 16 * 3600      # ...otherwise it must be from the same noon-to-noon night and no older than this
RESIDENT_STATE = ("in_bed", "zone", "hr", "motion", "up_since", "quiet_until", "last_exit")


@dataclass
class Resident:
    id: str
    name: str
    room: str
    wander_risk: float
    hr_rest: float
    pattern: str
    what_helped: list[list[object]]
    description: str
    x: float
    y: float
    in_bed: bool = True
    zone: str = "room"
    hr: float | None = None
    motion: float = 0.03
    up_since: float | None = None   # virtual epoch seconds
    unusual: float = 0.0
    unusual_streak: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=360))  # (clock, unusual, hr, zone, in_bed)
    quiet_until: float = 0.0       # real time; staff are with the resident, no new heads-ups
    last_exit: str | None = None

    def suggestions(self) -> list[str]:
        ranked = sorted(self.what_helped, key=lambda h: (-int(h[1]) / max(int(h[2]), 1), -int(h[2])))
        return [f"{name}: settled them {helped} of {tried} times" for name, helped, tried in ranked]


@dataclass
class Exit:
    id: str
    label: str
    kind: str
    x: float
    y: float
    short: str = ""
    open: bool = False


@dataclass
class Alert:
    id: int
    level: int
    title: str
    resident: str | None
    exit: str | None
    created: float = field(default_factory=time.time)
    level_since: float = field(default_factory=time.time)
    acked_by: str | None = None
    acked_at: float | None = None
    resolved: str | None = None
    history: list[tuple[str, str]] = field(default_factory=list)
    contact_step: int = 0
    max_level: int = 0
    backup_by: str | None = None
    went_outside: bool = False
    record: dict[str, object] | None = None
    simulated: bool = False         # raised by the presenter's simulated camera, not a real sensor
    notifications: list[dict[str, object]] = field(default_factory=list)   # contact fan-out, one per contact+channel
    last_fanout: float = 0.0        # real time of the last fan-out (one per alert per 60 s)


class Engine:
    def __init__(self, facility_path: Path, scorer: object, l1_timeout: float, ack_timeout: float) -> None:
        self.facility_path = facility_path
        self.scorer = scorer
        self.l1_timeout = l1_timeout
        self.ack_timeout = ack_timeout
        self.lock = threading.RLock()
        self.latencies: deque[float] = deque(maxlen=300)
        self._scenario: threading.Thread | None = None
        self._scenario_stop = threading.Event()
        self._scenario_lock = threading.Lock()   # never held together with self.lock (the scenario thread needs it)
        self._changed = threading.Condition()     # own lock: taken after self.lock, never before it
        self.rev = 0
        self.on_log: Callable[[str, str], None] | None = None   # server hooks this into logs/nightwatch.log
        self._state_path: Path | None = None
        self._save_lock = threading.Lock()
        self._saved_rev = -1
        self._autosave_stop = threading.Event()
        self._autosave: threading.Thread | None = None
        self._simulating = False                  # True while a simulated camera frame is being handled
        self.notifier: Notifier | None = None     # contact fan-out (calls/SMS); the server sets it up
        self.reset()

    # ------------------------------------------------------------------ clock and setup

    def reset(self) -> None:
        with self.lock:
            spec = json.loads(self.facility_path.read_text())
            self.facility = spec["facility"]
            self.night_staff = spec["night_staff"]
            self.night_start, self.night_end = spec.get("night_hours", [NIGHT_START, NIGHT_END])
            self._ticks = 0
            self.outside_temp_c = spec["outside_temp_c"]
            self.residents = {r["id"]: Resident(**r) for r in spec["residents"]}
            self.exits = {e["id"]: Exit(**e) for e in spec["exits"]}
            self.cameras = {c["id"]: dict(c, streak=0, persons=0, last_frame=0.0, last_log=0.0, enabled=True, schedule="always")
                            for c in spec.get("cameras", [])}
            self.camera_latencies: deque[float] = deque(maxlen=300)
            self.alerts: list[Alert] = []
            self.events: deque[dict[str, str]] = deque(maxlen=60)
            self.visits_today = 0
            self._next_alert = 1
            self._base_real = time.time()
            self._base_virtual = self._base_real
            self.speed = 1.0
            self.bump()

    def bump(self) -> None:
        """Something changed: wake /api/events streams and the autosave."""
        with self._changed:
            self.rev += 1
            self._changed.notify_all()

    def wait_change(self, since: int, timeout: float) -> int:
        """Block until rev differs from `since` (or timeout); return the current rev."""
        with self._changed:
            self._changed.wait_for(lambda: self.rev != since, timeout)
            return self.rev

    def now(self) -> float:
        """Virtual epoch seconds (the demo can jump to 2 am)."""
        with self.lock:
            return self._base_virtual + (time.time() - self._base_real) * self.speed

    def clock(self) -> datetime:
        return datetime.fromtimestamp(self.now())

    def set_time(self, hhmm: str, speed: float | None = None) -> None:
        with self.lock:
            h, m = (int(v) for v in hhmm.split(":"))
            target = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
            self._base_real, self._base_virtual = time.time(), target.timestamp()
            if speed is not None:
                self.speed = speed
            self.log("clock", f"Clock set to {hhmm} ({'night' if self.is_night() else 'day'} mode)")

    def is_night(self) -> bool:
        h = self.clock().hour
        if self.night_start > self.night_end:              # e.g. 21:00 -> 05:00 crosses midnight
            return h >= self.night_start or h < self.night_end
        return self.night_start <= h < self.night_end

    def stamp(self) -> str:
        return self.clock().strftime("%H:%M:%S")

    def log(self, kind: str, text: str) -> None:
        with self.lock:
            self.events.appendleft({"t": self.stamp(), "kind": kind, "text": text})
            self.bump()
        if self.on_log:
            try:
                self.on_log(kind, text)
            except Exception:  # noqa: BLE001 - logging must never break the night
                pass

    # ------------------------------------------------------------------ sensor events

    def sensor(self, ev: dict[str, object]) -> dict[str, object]:
        with self.lock:
            kind = ev.get("type")
            r = self.residents.get(str(ev.get("resident", "")))
            if kind == "door":
                return self._door(str(ev["exit"]), bool(ev.get("open", True)))
            if r is None:
                return {"error": f"unknown resident {ev.get('resident')!r}"}
            if kind == "bed":
                r.in_bed = bool(ev["in_bed"])
                r.up_since = None if r.in_bed else self.now()
                # Until the wearable reports, assume light movement when up and stillness in bed.
                r.motion = 0.03 if r.in_bed else max(r.motion, 0.25)
                if r.in_bed:
                    r.zone = "room"
                self.log("sensor", f"{r.name} {'back in bed' if r.in_bed else 'out of bed'}")
            elif kind == "hr":
                r.hr = float(ev["bpm"])  # type: ignore[arg-type]
            elif kind == "motion":
                r.motion = float(ev["level"])  # type: ignore[arg-type]
            elif kind == "location":
                zone = str(ev["zone"])
                if zone != r.zone:
                    r.zone = zone
                    if zone != "room" and r.in_bed:
                        r.in_bed, r.up_since = False, r.up_since or self.now()
                    if zone != "room":
                        r.motion = max(r.motion, 0.3)  # moving between zones means walking
                    self.log("sensor", f"{r.name} -> {zone}")
                    if self.is_night() and zone in ("corridor", "common", "exit") and time.time() >= r.quiet_until:
                        # The rule that matters at night: a resident has left their room.
                        self._raise(1, f"{r.name} left their room at {self.clock():%H:%M}", r, None)
                    if zone == "outside":
                        self._outside(r, str(ev.get("exit") or r.last_exit or "an exit"))
            else:
                return {"error": f"unknown event type {kind!r}"}
            self.bump()
            return {"ok": True}

    def _door(self, exit_id: str, opened: bool) -> dict[str, object]:
        ex = self.exits.get(exit_id)
        if ex is None:
            return {"error": f"unknown exit {exit_id!r}"}
        ex.open = opened
        if not opened:
            self.log("door", f"{ex.label} closed")
            return {"ok": True}
        near = sorted((r for r in self.residents.values() if not r.in_bed and r.zone in ("exit", "corridor")),
                      key=lambda r: (r.zone != "exit", -r.wander_risk))
        who = near[0] if near else None
        if who:
            who.last_exit = ex.id
        if self.is_night() or ex.kind == "window":
            level = 2 if self.is_night() else 1
            title = f"{ex.label} opened at {self.clock():%H:%M}" + (f": {who.name} nearby" if who else "")
            self._raise(level, title, who, ex)
        else:
            self.visits_today += 1
            self.log("door", f"{ex.label} opened (day: logged as visit #{self.visits_today})")
            if who and who.wander_risk >= 0.7 and who.zone == "exit":
                self._raise(1, f"{who.name} (high wander risk) at the {ex.label.lower()} while it is open", who, ex)
        return {"ok": True}

    def _outside(self, r: Resident, exit_id: str) -> None:
        ex = self.exits.get(exit_id)
        label = ex.label if ex else exit_id
        self._raise(3, f"{r.name} is OUTSIDE via {label}. {self.outside_temp_c:g} °C", r, ex, force=True, outside=True)

    def camera_active(self, cam_id: str) -> tuple[bool, str]:
        """Should frames from this camera be analysed right now? (False = drop them unseen.)"""
        with self.lock:
            cam = self.cameras.get(cam_id)
            if cam is None:
                return False, "unknown camera"
            if not cam["enabled"]:
                return False, "turned off"
            if cam["schedule"] == "night" and not self.is_night():
                return False, "night only (21:00-05:00)"
            return True, ""

    def set_camera(self, cam_id: str, enabled: bool | None = None, schedule: str | None = None,
                   by: str = "staff") -> dict[str, object]:
        """Turn a camera's analysis on/off or set it to night-only. Logged, so the record shows who did it."""
        with self.lock:
            cam = self.cameras.get(cam_id)
            if cam is None:
                return {"error": f"unknown camera {cam_id!r}"}
            if enabled is not None and enabled != cam["enabled"]:
                cam["enabled"] = enabled
                self.log("camera", f"{cam['label']} turned {'ON' if enabled else 'OFF'} by {by}")
            if schedule in ("always", "night") and schedule != cam["schedule"]:
                cam["schedule"] = schedule
                self.log("camera", f"{cam['label']}: {'night only (21:00-05:00)' if schedule == 'night' else 'always on'} ({by})")
            if not self.camera_active(cam_id)[0]:
                cam["persons"], cam["streak"], cam["tracks"] = 0, 0, []
            return {"ok": True}

    def camera(self, cam_id: str, persons: int, ms: float,
               crossings: list[dict[str, object]] | None = None, doorway: bool = False) -> dict[str, object]:
        """One analysed frame: a person count and doorway crossings. The frame itself is already gone.

        With a doorway line drawn, only crossings count (precise: walking along the hallway or a
        staff member passing by doesn't fire). Without one, a person confirmed in view counts.
        """
        with self.lock:
            cam = self.cameras.get(cam_id)
            if cam is None:
                return {"error": f"unknown camera {cam_id!r}"}
            self.camera_latencies.append(ms)
            if persons != cam["persons"] or time.time() - cam["last_frame"] >= 3:
                self.bump()   # count changed, or the camera just came (back) live
            cam["persons"], cam["last_frame"] = persons, time.time()
            if doorway:
                for c in crossings or []:
                    self._crossing(cam, str(c["direction"]))
                return {"ok": True}
            cam["streak"] = cam["streak"] + 1 if persons else 0
            if cam["streak"] != CAMERA_CONFIRM_FRAMES:   # act once, when a person is confirmed
                return {"ok": True}
            r = self.residents.get(cam.get("resident") or "")
            if not self.is_night():
                if time.time() - cam["last_log"] > 60:
                    cam["last_log"] = time.time()
                    self.log("camera", f"{cam['label']}: person seen (day: logged only)")
                return {"ok": True}
            if cam["zone"] == "exit":
                ex = self.exits.get(cam.get("exit") or "")
                near = r or next((x for x in sorted(self.residents.values(), key=lambda x: -x.wander_risk)
                                  if not x.in_bed), None)
                self._raise(2, f"Camera: person at the {cam['label'].replace(' camera', '').lower()} at {self.clock():%H:%M}", near, ex)
            elif r is not None and r.zone == "room":
                self.log("camera", f"{cam['label']}: person leaving room {r.room}")
                self.sensor({"type": "location", "resident": r.id, "zone": cam["zone"]})
            return {"ok": True}

    def _crossing(self, cam: dict[str, object], direction: str) -> None:
        r = self.residents.get(str(cam.get("resident") or ""))
        where = str(cam["label"]).replace(" camera", "").lower()
        if not self.is_night():
            self.log("camera", f"{cam['label']}: someone crossed the doorway {direction} (day: logged only)")
            return
        if cam["zone"] == "exit":
            if direction == "out":
                ex = self.exits.get(str(cam.get("exit") or ""))
                near = r or next((x for x in sorted(self.residents.values(), key=lambda x: -x.wander_risk)
                                  if not x.in_bed), None)
                self._raise(2, f"Camera: someone went out through the {where} at {self.clock():%H:%M}", near, ex)
            return
        if r is None:
            return
        if direction == "out" and r.zone == "room":
            self.log("camera", f"{cam['label']}: crossed the doorway out of room {r.room}")
            self.sensor({"type": "location", "resident": r.id, "zone": str(cam["zone"])})
        elif direction == "in" and r.zone != "room":
            self.log("camera", f"{cam['label']}: crossed back into room {r.room}")
            r.zone = "room"
            a = self._open_alert_for(r, None)
            if a and a.level == 1 and not a.exit:
                self._close(a, "Went back into their room (camera)", by_staff=False)

    # ------------------------------------------------------------------ alerts

    def _open_alert_for(self, r: Resident | None, ex: Exit | None) -> Alert | None:
        for a in self.alerts:
            if a.resolved:
                continue
            if (r and a.resident == r.id) or (ex and a.exit == ex.id and a.resident is None):
                return a
        return None

    def _raise(self, level: int, title: str, r: Resident | None, ex: Exit | None, force: bool = False,
               outside: bool = False) -> Alert:
        if self._simulating:
            title += " (simulated camera)"   # stays in the title and history after the radar tag is gone
        a = self._open_alert_for(r, ex)
        if a is None:
            a = Alert(self._next_alert, level, title, r.id if r else None, ex.id if ex else None,
                      simulated=self._simulating)
            self._next_alert += 1
            self.alerts.append(a)
            a.max_level = level
            a.history.append((self.stamp(), f"Level {level}: {title}"))
            self.log(f"L{level}", title)
        elif level > a.level or force:
            a.title = title
            a.simulated = a.simulated or self._simulating
            if ex and not a.exit:
                a.exit = ex.id
            self._set_level(a, max(level, a.level), title)
        if outside:
            a.went_outside = True   # before the contact fan-out, so the message says so
        if a.level == 3 and a.contact_step == 0:
            self._contact(a)
        return a

    def _set_level(self, a: Alert, level: int, why: str) -> None:
        if level == a.level and a.history:
            return
        a.level, a.level_since = level, time.time()
        a.max_level = max(a.max_level, level)
        if level >= 2:
            a.acked_by, a.acked_at = None, None  # a new urgent fact needs a fresh acknowledgement
        a.history.append((self.stamp(), f"Level {level}: {why}"))
        self.log(f"L{level}", f"Alert #{a.id} -> level {level}: {why}")

    def _contact(self, a: Alert) -> None:
        """Walk the contact chain one step. Never dials in this build."""
        steps = [
            "All staff phones: emergency alarm",
            "Calling on-call supervisor (simulated)",
            "Emergency services would be called now per facility policy (simulated: this demo never dials)",
        ]
        if a.contact_step < len(steps):
            a.history.append((self.stamp(), steps[a.contact_step]))
            self.log("contact", f"Alert #{a.id}: {steps[a.contact_step]}")
            if a.contact_step == 0 and not a.notifications:
                self._fanout(a, "NightWatch (emergency level reached)")
            a.contact_step += 1
            a.level_since = time.time()

    # ------------------------------------------------------------------ contact fan-out (calls / SMS)

    def notify(self, alert_id: int, by: str) -> dict[str, object]:
        """Staff pressed "Call for help": tell the contact list about this alert now."""
        with self.lock:
            a, err = self._open_alert(alert_id)
            if a is None:
                return err  # type: ignore[return-value]
            if self.notifier is None:
                return {"error": "contacts are not set up on this server"}
            wait = FANOUT_GAP_S - (time.time() - a.last_fanout)
            if wait > 0:
                return {"error": f"contacts were already notified for alert #{a.id}; try again in {int(wait) + 1} s"}
            return {"ok": True, **self._fanout(a, by[:40] or "Staff")}

    def _notify_text(self, a: Alert) -> str:
        """What contacts hear/read: alert facts only. Never video, never health readings."""
        r = self.residents.get(a.resident or "")
        ex = self.exits.get(a.exit or "")
        level = {1: "Heads-up", 2: "Urgent", 3: "Emergency"}.get(a.level, "Alert")
        if r:
            who = r.name if r.id in r.name else f"{r.name} ({r.id})"
            where = ("is outside the building" + (f", via the {ex.label.lower()}" if ex else "")) if a.went_outside \
                else f"is near the {ex.label.lower()}" if ex else f"is out of their room (room {r.room})"
            what = f"{who} {where}."
        else:
            what = f"{ex.label} opened." if ex else "An alert needs a response."
        temp = f" Outside temperature {self.outside_temp_c:g} degrees C." if a.went_outside else ""
        return (f"{PREFIX} {level} alert, level {a.level} of 3, at {self.facility}. {what}{temp} "
                f"Time {self.clock():%H:%M}. Night staff phones have been alerted.")

    def _fanout(self, a: Alert, by: str) -> dict[str, object]:
        """Tell each contact: real call/SMS only when live, configured and within the limits; else simulated.
        Network sends run in background threads and report back into the alert's history."""
        n = self.notifier
        if n is None:
            return {"notified": 0, "real": 0}
        a.last_fanout = time.time()
        text = self._notify_text(a)
        a.history.append((self.stamp(), f"Contacting the call list ({by})" + ("" if n.live else ": dry run, live calls are off")))
        real = 0
        r = self.residents.get(a.resident or "")
        who = r.name if r else ""
        for c in n.contacts_for(a.resident):
            name, role = str(c["name"]), str(c["role"])
            channels = channel_parts(str(c["channel"]))
            if role == "emergency_services":
                channels = [channels[0]]
            for ch in channels:
                entry: dict[str, object] = {"contact_id": c["id"], "role": role, "name": name, "channel": ch,
                                            "status": "queued", "detail": "", "at": self.stamp()}
                a.notifications.append(entry)
                if role == "emergency_services":
                    entry.update(status="simulated", detail="would be called per facility policy (simulated)")
                    self._contact_line(a, f"{name}: would be called per facility policy (simulated)")
                    continue
                to = recipient(c, ch)
                reason = ("simulated contact" if ch == "simulated"
                          else "simulated camera alert" if a.simulated
                          else "dry run, live calls are off" if not n.live
                          else f"no recipient for {ch}" if not to
                          else n.unavailable(ch)
                          or (f"limit of {MAX_REAL_PER_FANOUT} real messages per alert" if real >= MAX_REAL_PER_FANOUT
                              else "hourly limit reached" if not n.take_budget() else None))
                if reason:
                    entry.update(status="simulated", detail=reason)
                    self._contact_line(a, f"{name}: would {VERBS.get(ch, 'contact')} (simulated: {reason})")
                    continue
                real += 1
                entry["status"] = "calling" if ch == "call" else "queued"
                threading.Thread(target=self._send, args=(n, a, entry, ch, to, name, text, who),
                                 daemon=True, name=f"notify-{a.id}-{c['id']}-{ch}").start()
        self.bump()
        return {"notified": len(a.notifications), "real": real}

    def _send(self, n: Notifier, a: Alert, entry: dict[str, object], ch: str, to: str, name: str, text: str,
              who: str = "") -> None:
        status, detail = n.send(ch, to, text, a.level, who)   # network, no locks held
        with self.lock:
            entry.update(status=status, detail=detail, at=self.stamp())
            self._contact_line(a, self._result_line(ch, name, status, detail))
            self.bump()

    @staticmethod
    def _result_line(ch: str, name: str, status: str, detail: str) -> str:
        label = LABELS.get(ch, ch)
        if status == "calling":
            return f"Calling {name} … ringing"
        if status == "sent":
            return f"{label} to {name} sent"
        if status == "simulated":
            return f"{name}: not reached ({detail})"
        return f"{label} to {name} failed: {detail}"

    def test_contact(self, cid: str) -> dict[str, object]:
        """One-off "test message" to one contact, so staff can check their phone before the night.
        Dry run (live off): says what would be sent. Blocks while sending (at most ~10 s per channel)."""
        n = self.notifier
        if n is None:
            return {"error": "contacts are not set up on this server"}
        c = next((dict(x) for x in list(n.contacts) if x["id"] == cid), None)
        if c is None:
            return {"error": f"unknown contact {cid!r}"}
        if n.tested_recently(cid):
            return {"error": f"a test was already sent to {c['name']} in the last 10 s"}
        text = TEST_TEXT
        results = []
        for ch in channel_parts(str(c["channel"])):
            to = recipient(c, ch)
            item: dict[str, object] = {"channel": ch, "to": to, "text": text}
            if c["role"] == "emergency_services":
                item.update(status="simulated", detail="emergency services are always simulated")
            elif ch == "simulated":
                item.update(status="simulated", detail="simulated contact")
            elif not to:
                item.update(status="simulated", detail=f"no recipient for {ch}")
            elif (why := n.unavailable(ch)) is not None:
                item.update(status="simulated", detail=why)
            elif not n.live:
                item.update(status="dry_run", detail="live is off: this is what would be sent")
            elif not n.take_budget():
                item.update(status="simulated", detail="hourly limit reached")
            else:
                status, detail = n.send(ch, to, text, 1, "test")
                item.update(status=status, detail=detail)
            results.append(item)
        summary = ", ".join(f"{x['channel']}: {x['status']}" for x in results)
        self.log("contact", f"Test message to {c['name']} ({summary})")
        return {"ok": True, "live": n.live, "contact": c["id"], "results": results,
                "masked": [dict(x, to=mask_any(str(x["to"]))) for x in results]}

    def _contact_line(self, a: Alert, line: str) -> None:
        a.history.append((self.stamp(), line))
        self.log("contact", f"Alert #{a.id}: {line}")

    def _open_alert(self, alert_id: int) -> tuple[Alert | None, dict[str, object] | None]:
        a = next((x for x in self.alerts if x.id == alert_id), None)
        if a is None:
            return None, {"error": f"no such open alert (#{alert_id} doesn't exist)"}
        if a.resolved:
            return None, {"error": f"alert #{alert_id} is already resolved"}
        return a, None

    def ack(self, alert_id: int, by: str) -> dict[str, object]:
        with self.lock:
            a, err = self._open_alert(alert_id)
            if a is None:
                return err  # type: ignore[return-value]
            a.acked_by, a.acked_at = by, time.time()
            secs = round(a.acked_at - a.created)
            a.history.append((self.stamp(), f"Acknowledged by {by} ({secs} s after first alert)"))
            self.log("ack", f"Alert #{a.id} acknowledged by {by} after {secs} s")
            return {"ok": True}

    def resolve(self, alert_id: int, outcome: str) -> dict[str, object]:
        with self.lock:
            a, err = self._open_alert(alert_id)
            if a is None:
                return err  # type: ignore[return-value]
            self._close(a, outcome, by_staff=True)
            r = self.residents.get(a.resident or "")
            if r:
                # Staff walked them back: settled in their room, and staff stay close for a while.
                r.zone, r.in_bed, r.up_since, r.motion = "room", True, None, 0.03
                r.hr = r.hr_rest + 8 if r.hr else None
                r.quiet_until = time.time() + 120
            if a.exit and a.exit in self.exits:
                self.exits[a.exit].open = False
            return {"ok": True}

    def _close(self, a: Alert, outcome: str, by_staff: bool) -> None:
        a.resolved = outcome
        a.record = self._care_record(a, by_staff)
        a.history.append((self.stamp(), f"Resolved: {outcome} · severity {a.record['severity']} {a.record['severity_label']}"))
        self.log("resolved", f"Alert #{a.id} resolved: {outcome} (severity {a.record['severity']} {a.record['severity_label']})")

    def _care_record(self, a: Alert, by_staff: bool) -> dict[str, object]:
        """Draft entry in the care team's event-record format (taxonomy v2).

        Location is recorded as a type, never a room, and time as a band at night. Affect and the
        intervention actually used are left for staff to confirm: the system doesn't know them.
        """
        night = self.is_night()
        h = self.clock().hour
        earlier = sum(1 for x in self.alerts if x.resident == a.resident and x.id != a.id and x.created < a.created)
        severity = max(a.max_level, 1)
        return {
            "resident": a.resident,
            "category": "Behaviour and distress",
            "behaviour": "Exit-seeking or wandering" if (a.exit or a.went_outside) else "Restlessness",
            "affect": "(staff to confirm)",
            "frequency": "Isolated" if earlier == 0 else "Repeated",
            "time_of_day": "Night" if night else "Morning" if h < 12 else "Afternoon" if h < 18 else "Evening",
            "night_band": "Night (21:00-05:00)" if night else None,
            "location_type": "Exterior (left the building)" if a.went_outside
                             else "Communal / circulation" if a.exit else "Own room",
            "trigger": "Night / unsettled" if night else None,
            "intervention": "(staff to confirm)" if by_staff else "Monitoring",
            "outcome": "Escalated for clinical review" if severity == 3 else "Managed without further escalation",
            "severity": severity,
            "severity_label": SEVERITY[severity],
            "draft_by": "NightWatch: confirm affect and intervention before filing",
            "status": "draft",
        }

    # ------------------------------------------------------------------ the 1 s loop

    def tick(self) -> None:
        with self.lock:
            hour = self.clock().hour + self.clock().minute / 60
            night = self.is_night()
            before = [round(r.unusual, 3) for r in self.residents.values()]
            for r in self.residents.values():
                minutes_up = 0.0 if r.up_since is None else (self.now() - r.up_since) / 60
                x = vector(hour, not r.in_bed, r.motion, r.hr, r.hr_rest, r.zone, minutes_up)
                r.unusual, ms = self.scorer.score(x)  # type: ignore[attr-defined]
                self.latencies.append(ms)
                if self._ticks % HISTORY_EVERY_S == 0:
                    r.history.append((self.clock().strftime("%H:%M:%S"), round(r.unusual, 3), r.hr, r.zone, r.in_bed))
                r.unusual_streak = r.unusual_streak + 1 if (night and not r.in_bed and r.unusual >= UNUSUAL_ALERT) else 0
                if r.unusual_streak >= SUSTAIN_S and r.zone != "outside" and time.time() >= r.quiet_until:
                    if self._open_alert_for(r, None) is None:
                        hr = f", heart rate {r.hr:.0f} (rest {r.hr_rest:.0f})" if r.hr else ""
                        self._raise(1, f"{r.name} is up at {self.clock():%H:%M}, unusual for them{hr}", r, None)
            if self._ticks % HISTORY_EVERY_S == 0 or before != [round(r.unusual, 3) for r in self.residents.values()]:
                self.bump()
            self._ticks += 1
            now = time.time()
            for a in self.alerts:
                if a.resolved:
                    continue
                r = self.residents.get(a.resident or "")
                if a.level == 1 and r and r.in_bed and not a.exit:
                    # A heads-up clears itself when the resident settles back into bed.
                    self._close(a, "Settled back in bed on their own", by_staff=False)
                    continue
                if a.acked_by:
                    continue
                waited = now - a.level_since
                if a.level == 1 and waited >= self.l1_timeout and r and r.unusual >= UNUSUAL_ALERT:
                    self._set_level(a, 2, f"no response for {int(waited)} s")
                elif a.level == 2 and waited >= self.ack_timeout:
                    self._set_level(a, 3, f"not acknowledged within {int(waited)} s")
                    self._contact(a)
                elif a.level == 3 and waited >= self.ack_timeout:
                    self._contact(a)

    # ------------------------------------------------------------------ staff actions

    def confirm_record(self, alert_id: int, affect: str, intervention: str, nondrug_alt: str | None,
                       note: str, by: str) -> dict[str, object]:
        """Staff confirm the two fields the system can't know, and file the record."""
        with self.lock:
            a = next((x for x in self.alerts if x.id == alert_id), None)
            if a is None:
                return {"error": f"no such alert (#{alert_id} doesn't exist)"}
            if not a.record:
                return {"error": f"alert #{alert_id} is still open: resolve it before filing the record"}
            if affect not in TAXONOMY["affect"]:
                return {"error": f"affect must be one of {TAXONOMY['affect']}"}
            if intervention not in TAXONOMY["intervention"]:
                return {"error": f"intervention must be one of {TAXONOMY['intervention']}"}
            if nondrug_alt and nondrug_alt not in TAXONOMY["nondrug_alt"]:
                return {"error": f"nondrug_alt must be one of {TAXONOMY['nondrug_alt']}"}
            a.record.update({"affect": affect, "intervention": intervention, "nondrug_alt": nondrug_alt or None,
                             "note": note.strip()[:500] or None, "status": "filed", "confirmed_by": by[:40],
                             "confirmed_at": self.stamp()})
            a.record.pop("draft_by", None)
            self.log("record", f"Care record for alert #{a.id} filed by {by[:40]}")
            return {"ok": True, "record": a.record}

    def request_backup(self, alert_id: int, by: str) -> dict[str, object]:
        """A responder asks for help: every staff phone is alerted, at least at urgent level."""
        with self.lock:
            a, err = self._open_alert(alert_id)
            if a is None:
                return err  # type: ignore[return-value]
            if a.level < 2:
                self._set_level(a, 2, f"backup requested by {by[:40]}")
            a.backup_by = by[:40]
            a.history.append((self.stamp(), f"Backup requested by {by[:40]}: all staff phones alerted"))
            self.log("contact", f"Alert #{a.id}: backup requested by {by[:40]}")
            return {"ok": True}

    def resident_detail(self, rid: str) -> dict[str, object]:
        with self.lock:
            r = self.residents.get(rid)
            if r is None:
                return {"error": f"unknown resident {rid!r}"}
            return {
                "id": r.id, "name": r.name, "room": r.room, "risk": r.wander_risk, "hr_rest": r.hr_rest,
                "pattern": r.pattern, "description": r.description, "suggestions": r.suggestions(),
                "now": {"in_bed": r.in_bed, "zone": r.zone, "hr": r.hr, "unusual": round(r.unusual, 3),
                        "minutes_up": None if r.up_since is None else round((self.now() - r.up_since) / 60, 1)},
                "history": [{"t": t, "unusual": u, "hr": hr, "zone": z, "in_bed": b} for t, u, hr, z, b in r.history],
                "alerts": [self._alert_view(a) for a in self.alerts if a.resident == rid],
            }

    # ------------------------------------------------------------------ settings (laptop only)

    def settings(self) -> dict[str, object]:
        with self.lock:
            spec = json.loads(self.facility_path.read_text())
            spec.setdefault("night_hours", [NIGHT_START, NIGHT_END])
            spec["timeouts"] = {"l1": self.l1_timeout, "ack": self.ack_timeout}
            spec["source"] = self.facility_path.name
            return spec

    def apply_settings(self, new: dict[str, object], save_to: Path) -> dict[str, object]:
        """Validate, save to a local (git-ignored) file, and apply it.

        Kept across the change: each camera's on/off, schedule and doorway line (by id: a camera staff
        turned off stays off), tonight's resolved alerts with their care records (draft or filed), the
        event log, visit count, alert numbering and the clock. Only open alerts and residents' live
        state start over; the response says how many open alerts were cleared."""
        try:
            spec = validate_settings(new)
        except ValueError as exc:
            return {"error": str(exc)}
        except (TypeError, AttributeError, IndexError, KeyError):
            return {"error": "settings are malformed: expected the same shape as GET /api/settings"}
        timeouts = spec.pop("timeouts", None)
        with self.lock:
            save_to.parent.mkdir(parents=True, exist_ok=True)
            save_to.write_text(json.dumps(spec, indent=2))
            if timeouts:
                self.l1_timeout, self.ack_timeout = float(timeouts["l1"]), float(timeouts["ack"])
            self.facility_path = save_to
            cams = {cid: {k: c.get(k) for k in ("enabled", "schedule", "doorway")} for cid, c in self.cameras.items()}
            kept = [a for a in self.alerts if a.resolved]
            dropped = len(self.alerts) - len(kept)
            events, visits, next_id = list(self.events), self.visits_today, self._next_alert
            clock = (self._base_real, self._base_virtual, self.speed)
            self.reset()
            for cid, c in cams.items():
                if cid in self.cameras:
                    self.cameras[cid].update(enabled=c["enabled"], schedule=c["schedule"])
                    if c["doorway"]:
                        self.cameras[cid]["doorway"] = c["doorway"]
            self.alerts, self.visits_today, self._next_alert = kept, visits, next_id
            self.events.extend(events)   # appendleft order: newest first, so extend keeps it
            self._base_real, self._base_virtual, self.speed = clock
            drafts = sum(1 for a in kept if a.record and a.record.get("status") == "draft")
            self.log("settings", "Settings saved and applied" + (f" ({dropped} open alert(s) cleared)" if dropped else ""))
        if not kept and not dropped:
            return {"ok": True}
        return {"ok": True, "kept_alerts": len(kept), "draft_records_kept": drafts, "open_alerts_cleared": dropped}

    # ------------------------------------------------------------------ view

    def view(self) -> dict[str, object]:
        with self.lock:
            lat = sorted(self.latencies)
            open_alerts = [a for a in self.alerts if not a.resolved]
            acked = [a.acked_at - a.created for a in self.alerts if a.acked_at]
            return {
                "facility": self.facility, "night_staff": self.night_staff, "outside_temp_c": self.outside_temp_c,
                "clock": self.clock().strftime("%H:%M:%S"), "night": self.is_night(), "speed": self.speed,
                "scorer": {"name": self.scorer.name, "device": self.scorer.device,  # type: ignore[attr-defined]
                           "p50_ms": round(lat[len(lat) // 2], 3) if lat else None,
                           "p95_ms": round(lat[int(len(lat) * 0.95)], 3) if lat else None},
                "timeouts": {"l1": self.l1_timeout, "ack": self.ack_timeout},
                "contacts_live": bool(self.notifier and self.notifier.live),
                "twilio_configured": bool(self.notifier and self.notifier.configured()),
                "channels": self.notifier.available() if self.notifier else
                            {"call": False, "sms": False, "push": False, "imessage": False},
                "night_hours": [self.night_start, self.night_end],
                "taxonomy": TAXONOMY,
                "cameras": [{"id": c["id"], "label": c["label"], "persons": c["persons"],
                             "live": time.time() - c["last_frame"] < 3, "doorway": c.get("doorway"),
                             "tracks": c.get("tracks", []) if time.time() - c["last_frame"] < 3 else [],
                             "simulated": bool(c.get("simulated")) and time.time() - c["last_frame"] < 3,
                             "enabled": c["enabled"], "schedule": c["schedule"],
                             "active": self.camera_active(c["id"])[0], "paused_reason": self.camera_active(c["id"])[1]}
                            for c in self.cameras.values()],
                "camera_p50_ms": round(sorted(self.camera_latencies)[len(self.camera_latencies) // 2], 1)
                                 if self.camera_latencies else None,
                "residents": [
                    {"id": r.id, "name": r.name, "room": r.room, "risk": r.wander_risk, "in_bed": r.in_bed,
                     "zone": r.zone, "hr": r.hr, "hr_rest": r.hr_rest, "unusual": round(r.unusual, 3),
                     "pattern": r.pattern, "description": r.description, "x": r.x, "y": r.y,
                     "suggestions": r.suggestions()}
                    for r in self.residents.values()
                ],
                "exits": [vars(e) for e in self.exits.values()],
                "alerts": [self._alert_view(a) for a in sorted(open_alerts, key=lambda a: (-a.level, a.created))],
                "recent": [self._alert_view(a) for a in self.alerts if a.resolved][-5:],
                "events": list(self.events),
                "stats": {"visits_today": self.visits_today, "alerts": len(self.alerts),
                          "median_ack_s": round(sorted(acked)[len(acked) // 2]) if acked else None},
            }

    def summary(self) -> dict[str, object]:
        """The compact "something changed" ping for /api/events (enough to sound an alarm without a fetch)."""
        with self.lock:
            open_alerts = [a for a in self.alerts if not a.resolved]
            return {"rev": self.rev, "clock": self.stamp(), "night": self.is_night(),
                    "open_alerts": len(open_alerts), "top_level": max((a.level for a in open_alerts), default=0),
                    "unacked": sum(1 for a in open_alerts if not a.acked_by), "alerts": len(self.alerts),
                    "last_event": self.events[0] if self.events else None}

    def report(self) -> dict[str, object]:
        """Morning handover: everything that happened tonight, with response times and care records."""
        with self.lock:
            acked = sorted(round(a.acked_at - a.created) for a in self.alerts if a.acked_at)
            sev: dict[str, int] = {"1": 0, "2": 0, "3": 0}
            for a in self.alerts:
                if a.record:
                    sev[str(a.record["severity"])] += 1
            by_resident: dict[str, int] = {}
            for a in self.alerts:
                if a.resident:
                    by_resident[a.resident] = by_resident.get(a.resident, 0) + 1
            return {
                "facility": self.facility, "generated_at": self.clock().strftime("%H:%M"),
                "night_staff": self.night_staff,
                "totals": {"alerts": len(self.alerts), "open": sum(1 for a in self.alerts if not a.resolved),
                           "reached_emergency": sum(1 for a in self.alerts if a.max_level >= 3),
                           "went_outside": sum(1 for a in self.alerts if a.went_outside),
                           "settled_on_their_own": sum(1 for a in self.alerts if a.record and a.record["intervention"] == "Monitoring"),
                           "visits_logged": self.visits_today,
                           "records_filed": sum(1 for a in self.alerts if a.record and a.record.get("status") == "filed"),
                           "records_draft": sum(1 for a in self.alerts if a.record and a.record.get("status") == "draft")},
                "response_s": {"median": acked[len(acked) // 2] if acked else None, "max": acked[-1] if acked else None,
                               "count": len(acked)},
                "severity": sev,
                "by_resident": by_resident,
                "alerts": [self._alert_view(a) for a in self.alerts],
                "model": {"name": self.scorer.name, "device": self.scorer.device},  # type: ignore[attr-defined]
            }

    def _alert_view(self, a: Alert) -> dict[str, object]:
        r = self.residents.get(a.resident or "")
        due = None
        if not a.resolved and not a.acked_by:
            limit = self.l1_timeout if a.level == 1 else self.ack_timeout
            due = max(0, round(limit - (time.time() - a.level_since)))
        return {"id": a.id, "level": a.level, "title": a.title, "resident": a.resident, "exit": a.exit,
                "age_s": round(time.time() - a.created), "acked_by": a.acked_by, "resolved": a.resolved,
                "escalates_in_s": due, "history": a.history,
                "description": r.description if r else None, "room": r.room if r else None,
                "suggestions": r.suggestions()[:2] if r else [],
                "max_level": a.max_level, "record": a.record, "backup_by": a.backup_by,
                "created": a.created, "simulated": a.simulated,
                "notifications": [dict(x) for x in a.notifications]}

    # ------------------------------------------------------------------ surviving a restart

    def snapshot(self) -> dict[str, object]:
        """Everything a restart must not lose, as plain JSON-able data (copied, so it's safe to write unlocked)."""
        with self.lock:
            return {
                "version": STATE_VERSION, "saved_at": time.time(), "rev": self.rev,
                "facility_file": self.facility_path.name, "facility": self.facility,
                "resident_ids": sorted(self.residents),
                "clock": {"virtual": self.now(), "speed": self.speed},
                "next_alert": self._next_alert, "visits_today": self.visits_today,
                "alerts": [asdict(a) for a in self.alerts],
                "events": list(self.events),
                "residents": {r.id: {k: getattr(r, k) for k in RESIDENT_STATE} | {"history": list(r.history)}
                              for r in self.residents.values()},
                "exits": {e.id: e.open for e in self.exits.values()},
                "cameras": {c["id"]: {"enabled": c["enabled"], "schedule": c["schedule"], "doorway": c.get("doorway")}
                            for c in self.cameras.values()},
            }

    @staticmethod
    def restorable(data: dict[str, object], now: float | None = None) -> tuple[bool, str]:
        """Is this saved state from tonight? (Same noon-to-noon night, or saved in the last 2 hours.)"""
        now = time.time() if now is None else now
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return False, "different state file version"
        try:
            saved = float(data["saved_at"])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            return False, "no save time"
        age = now - saved
        if age < -60:
            return False, "saved in the future (clock changed?)"
        if age <= SAME_NIGHT_GRACE_S:
            return True, f"saved {int(age)} s ago"
        night = lambda t: (datetime.fromtimestamp(t) - timedelta(hours=12)).date()  # noqa: E731
        if age <= MAX_STATE_AGE_S and night(saved) == night(now):
            return True, f"saved {int(age // 60)} min ago, same night"
        return False, f"from an earlier night ({int(age // 3600)} h old)"

    def restore(self, data: dict[str, object]) -> tuple[bool, str]:
        """Bring back a saved night. Anything that doesn't match this facility file is skipped; a broken file
        leaves a clean night (never a half-restored one)."""
        ok, why = self.restorable(data)
        if not ok:
            return False, why
        if data.get("facility_file") != self.facility_path.name or data.get("resident_ids") != sorted(self.residents):
            return False, "facility settings changed since it was saved"
        names = {f.name for f in fields(Alert)}
        with self.lock:
            try:
                alerts = []
                for d in data.get("alerts") or []:  # type: ignore[union-attr]
                    a = Alert(**{k: v for k, v in d.items() if k in names})
                    a.history = [(str(t), str(h)) for t, h in a.history]
                    alerts.append(a)
                ids = [a.id for a in alerts]
                if len(set(ids)) != len(ids):
                    raise ValueError("duplicate alert ids")
                for rid, st in (data.get("residents") or {}).items():  # type: ignore[union-attr]
                    r = self.residents.get(rid)
                    if r is None:
                        continue
                    for k in RESIDENT_STATE:
                        if k in st:
                            setattr(r, k, st[k])
                    r.history = deque((tuple(h) for h in st.get("history", [])), maxlen=r.history.maxlen)
                for xid, is_open in (data.get("exits") or {}).items():  # type: ignore[union-attr]
                    if xid in self.exits:
                        self.exits[xid].open = bool(is_open)
                for cid, c in (data.get("cameras") or {}).items():  # type: ignore[union-attr]
                    if cid in self.cameras:
                        cam = self.cameras[cid]
                        cam["enabled"] = bool(c.get("enabled", True))
                        cam["schedule"] = c.get("schedule") if c.get("schedule") in ("always", "night") else "always"
                        cam["doorway"] = c.get("doorway") or None
                clock = data["clock"]
                self._base_real, self._base_virtual = float(data["saved_at"]), float(clock["virtual"])  # type: ignore[arg-type,index]
                self.speed = float(clock["speed"])  # type: ignore[index]
                self.alerts = alerts
                self._next_alert = max([int(data.get("next_alert") or 1), *[i + 1 for i in ids]])  # type: ignore[arg-type]
                self.visits_today = int(data.get("visits_today") or 0)  # type: ignore[arg-type]
                self.events = deque((dict(e) for e in data.get("events") or []), maxlen=self.events.maxlen)  # type: ignore[union-attr]
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                self.reset()
                return False, f"unreadable ({exc!r}); started a clean night"
            n_open = sum(1 for a in alerts if not a.resolved)
            self.log("system", f"Server restarted: restored tonight's state ({len(alerts)} alerts, {n_open} open)")
        return True, why

    def save_now(self) -> bool:
        """Write the state file atomically (tmp + rename). Safe to call from any thread."""
        if self._state_path is None:
            return False
        with self._save_lock:
            snap = self.snapshot()
            path = self._state_path
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(snap, separators=(",", ":")))
                for attempt in range(5):
                    try:
                        os.replace(tmp, path)   # atomic; on Windows a reader holding the file can block it briefly
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(0.05)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                return False
            self._saved_rev = int(snap["rev"])  # type: ignore[arg-type]
            return True

    def start_autosave(self, path: Path, debounce: float = 1.0) -> None:
        """Save on every change, at most once per `debounce` seconds, in a background thread."""
        self._state_path = path
        self._autosave_stop.clear()

        def loop() -> None:
            while not self._autosave_stop.is_set():
                rev = self.wait_change(self._saved_rev, timeout=1.0)
                if rev == self._saved_rev:
                    continue
                if self._autosave_stop.wait(debounce):   # coalesce a burst of changes into one write
                    break
                try:
                    self.save_now()
                except Exception:  # noqa: BLE001 - keep saving on the next change
                    pass

        self._autosave = threading.Thread(target=loop, daemon=True, name="autosave")
        self._autosave.start()

    def stop_autosave(self, final_save: bool = True) -> None:
        self._autosave_stop.set()
        with self._changed:
            self._changed.notify_all()
        if self._autosave:
            self._autosave.join(timeout=3)
        if final_save:
            self.save_now()

    # ------------------------------------------------------------------ scripted demo scenarios

    def run_scenario(self, name: str) -> dict[str, object]:
        steps = SCENARIOS.get(name)
        if steps is None:
            return {"error": f"unknown scenario {name!r}", "choices": sorted(SCENARIOS)}
        # Its own lock, never self.lock: the old scenario thread may be waiting on self.lock while we join it.
        with self._scenario_lock:
            self._scenario_stop.set()
            if self._scenario and self._scenario.is_alive():
                self._scenario.join(timeout=2)
            self._scenario_stop = threading.Event()
            stop = self._scenario_stop

            def run() -> None:
                for delay, action in steps:
                    if stop.wait(delay):
                        return
                    try:
                        action(self)
                    except Exception as exc:  # noqa: BLE001 - one bad step must not kill the demo silently
                        self.log("system", f"Scenario {name}: step failed ({exc!r})")

            self._scenario = threading.Thread(target=run, daemon=True, name=f"scenario-{name}")
            self._scenario.start()
        return {"ok": True, "scenario": name}


VERBS = {"call": "call", "sms": "text", "push": "push", "imessage": "iMessage", "simulated": "contact"}
LABELS = {"call": "Call", "sms": "SMS", "push": "Push", "imessage": "iMessage"}
TEST_TEXT = "This is a NightWatch demo test message."


def _s(ev: dict[str, object]) -> Callable[[Engine], object]:
    return lambda e: e.sensor(ev)


def _unacked(rid: str, ev: dict[str, object]) -> Callable[[Engine], object]:
    """Only happens if nobody has responded to this resident's alert yet."""
    def act(e: Engine) -> None:
        open_alert = next((a for a in e.alerts if a.resident == rid and not a.resolved), None)
        if open_alert is not None and not open_alert.acked_by:
            e.sensor(ev)
    return act


def _hr_ramp(rid: str, start: int, end: int, steps: int, gap: float) -> list[tuple[float, Callable[[Engine], object]]]:
    return [(gap, _s({"type": "hr", "resident": rid, "bpm": start + (end - start) * i / (steps - 1)})) for i in range(steps)]


SIM_DOORWAY = {"line": [0.47, 0.40, 0.50, 0.86], "inside": 1}   # room is left of this line in the demo view


def _sim_camera_frame(i: int, n: int) -> Callable[[Engine], object]:
    """Presenter stand-in for the hallway camera: a person walks from the room out across the doorway.

    Feeds the same path as a real frame (tracks + crossing) and is flagged "simulated" in the state.
    """
    def act(e: Engine) -> None:
        if not e.camera_active("hall12")[0]:
            return
        with e.lock:
            cam = e.cameras["hall12"]
            cam["doorway"], cam["simulated"] = SIM_DOORWAY, True
            fx = 0.26 + 0.48 * i / (n - 1)                  # feet x from inside the room to the hallway
            trail = [[round(0.26 + 0.48 * k / (n - 1), 3), 0.85] for k in range(max(0, i - 7), i + 1)]
            cam["tracks"] = [{"id": 7, "x1": fx - 0.065, "y1": 0.40, "x2": fx + 0.065, "y2": 0.86,
                              "side": 1 if fx < 0.485 else -1, "confirmed": i >= 2, "trail": trail}]
            crossed = fx >= 0.485 and (0.26 + 0.48 * (i - 1) / (n - 1)) < 0.485
            e._simulating = True
            try:
                e.camera("hall12", 1, 6.0, [{"track": 7, "direction": "out"}] if crossed else [], doorway=True)
            finally:
                e._simulating = False
    return act


SCENARIOS: dict[str, list[tuple[float, Callable[[Engine], object]]]] = {
    # 02:08. Simulated hallway camera: a person walks out of room 12 across the doorway line.
    "camera_walkout": [
        (0, lambda e: (e.reset(), e.set_time("02:08"))),
        (1, _s({"type": "hr", "resident": "R-07", "bpm": 84})),
        *[(0.4, _sim_camera_frame(i, 16)) for i in range(16)],
        *[(0.4, _sim_camera_frame(15, 16)) for _ in range(20)],   # stays in the hallway, still in view
    ],
    # 02:08. R-07 gets up, gets agitated, heads for the front door, and walks out if nobody responds.
    "night_elopement": [
        (0, lambda e: (e.reset(), e.set_time("02:08"))),
        (1, _s({"type": "hr", "resident": "R-07", "bpm": 67})),
        (2, _s({"type": "bed", "resident": "R-07", "in_bed": False})),
        (1, _s({"type": "motion", "resident": "R-07", "level": 0.5})),
        *_hr_ramp("R-07", 72, 96, 6, 1.5),
        (1, _s({"type": "location", "resident": "R-07", "zone": "corridor"})),
        (6, _s({"type": "location", "resident": "R-07", "zone": "exit"})),
        (3, _s({"type": "door", "exit": "front", "open": True})),
        (40, _unacked("R-07", {"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})),
    ],
    # 03:00. R-15 makes a short ensuite trip. Normal for them: the system should stay quiet.
    "night_bathroom": [
        (0, lambda e: (e.reset(), e.set_time("03:00"))),
        (1, _s({"type": "hr", "resident": "R-15", "bpm": 63})),
        (2, _s({"type": "bed", "resident": "R-15", "in_bed": False})),
        (1, _s({"type": "motion", "resident": "R-15", "level": 0.3})),
        (1, _s({"type": "hr", "resident": "R-15", "bpm": 68})),
        (20, _s({"type": "bed", "resident": "R-15", "in_bed": True})),
    ],
    # 14:00. Visitors come and go; residents walk the corridors. Logged, no alarms.
    "day_visits": [
        (0, lambda e: (e.reset(), e.set_time("14:00"))),
        *[(0.5, _s({"type": "location", "resident": rid, "zone": z}))
          for rid, z in (("R-03", "common"), ("R-21", "corridor"), ("R-15", "common"), ("R-07", "common"))],
        (3, _s({"type": "door", "exit": "front", "open": True})),
        (2, _s({"type": "door", "exit": "front", "open": False})),
        (5, _s({"type": "door", "exit": "garden", "open": True})),
        (2, _s({"type": "door", "exit": "garden", "open": False})),
        (5, _s({"type": "door", "exit": "front", "open": True})),
        (2, _s({"type": "door", "exit": "front", "open": False})),
    ],
    "reset": [(0, lambda e: e.reset())],
}


def _num(v: object, lo: float, hi: float, what: str) -> float:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not lo <= f <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return f


def _text(v: object, n: int, what: str, required: bool = True) -> str:
    t = str(v or "").strip()
    if required and not t:
        raise ValueError(f"{what} is required")
    if len(t) > n:
        raise ValueError(f"{what} is longer than {n} characters")
    return t


def validate_settings(new: dict[str, object]) -> dict[str, object]:
    """Strict check of an edited facility file; raises ValueError with a message staff can act on."""
    import re

    ident = re.compile(r"^[A-Za-z0-9-]{1,12}$")
    out: dict[str, object] = {
        "_note": "Edited on this laptop in /settings/. Local only (git-ignored data/).",
        "facility": _text(new.get("facility"), 60, "Facility name"),
        "night_staff": int(_num(new.get("night_staff"), 1, 50, "Night staff")),
        "outside_temp_c": _num(new.get("outside_temp_c"), -50, 50, "Outside temperature"),
    }
    hours = new.get("night_hours") or [NIGHT_START, NIGHT_END]
    out["night_hours"] = [int(_num(hours[0], 0, 23, "Night starts")), int(_num(hours[1], 0, 23, "Night ends"))]  # type: ignore[index]
    if out["night_hours"][0] == out["night_hours"][1]:  # type: ignore[index]
        raise ValueError("Night start and end can't be the same hour")
    residents, seen = [], set()
    for i, r in enumerate(new.get("residents") or []):  # type: ignore[union-attr]
        rid = str(r.get("id", "")).strip()
        if not ident.match(rid) or rid in seen:
            raise ValueError(f"Resident {i + 1}: id must be unique, 1-12 letters/digits/dashes")
        seen.add(rid)
        helped = []
        for h in (r.get("what_helped") or [])[:8]:
            name, ok, tried = _text(h[0], 60, "What helped"), int(_num(h[1], 0, 999, "Times helped")), int(_num(h[2], 0, 999, "Times tried"))
            if ok > tried:
                raise ValueError(f"Resident {rid}: '{name}' helped more times than it was tried")
            helped.append([name, ok, tried])
        residents.append({
            "id": rid, "name": _text(r.get("name") or f"Resident {rid}", 40, "Name"), "room": _text(r.get("room"), 8, "Room"),
            "wander_risk": round(_num(r.get("wander_risk"), 0, 1, f"Resident {rid} wander risk"), 2),
            "hr_rest": _num(r.get("hr_rest"), 30, 150, f"Resident {rid} resting heart rate"),
            "pattern": _text(r.get("pattern"), 300, "Pattern", required=False),
            "what_helped": helped,
            "description": _text(r.get("description"), 120, "Description", required=False),
            "x": _num(r.get("x", 100), 0, 560, "x"), "y": _num(r.get("y", 70), 0, 290, "y"),
        })
    if not residents:
        raise ValueError("Add at least one resident")
    out["residents"] = residents
    exits, exit_ids = [], set()
    for i, x in enumerate(new.get("exits") or []):  # type: ignore[union-attr]
        xid = str(x.get("id", "")).strip()
        if not ident.match(xid) or xid in exit_ids:
            raise ValueError(f"Exit {i + 1}: id must be unique, 1-12 letters/digits/dashes")
        exit_ids.add(xid)
        kind = str(x.get("kind"))
        if kind not in ("exterior_door", "window"):
            raise ValueError(f"Exit {xid}: kind must be exterior_door or window")
        exits.append({"id": xid, "label": _text(x.get("label"), 40, "Exit label"), "kind": kind,
                      "short": _text(x.get("short") or x.get("label"), 24, "Short label"),
                      "x": _num(x.get("x", 470), 0, 560, "x"), "y": _num(x.get("y", 250), 0, 290, "y")})
    out["exits"] = exits
    cams, cam_ids = [], set()
    for i, c in enumerate(new.get("cameras") or []):  # type: ignore[union-attr]
        cid = str(c.get("id", "")).strip()
        if not ident.match(cid) or cid in cam_ids:
            raise ValueError(f"Camera {i + 1}: id must be unique, 1-12 letters/digits/dashes")
        cam_ids.add(cid)
        zone = str(c.get("zone"))
        if zone not in ("corridor", "exit"):
            raise ValueError(f"Camera {cid}: zone must be corridor (outside a room) or exit")
        res = c.get("resident") or None
        if res is not None and res not in seen:
            raise ValueError(f"Camera {cid}: resident {res} doesn't exist")
        ex = c.get("exit") or None
        if zone == "exit" and ex not in exit_ids:
            raise ValueError(f"Camera {cid}: pick which exit it watches")
        cam = {"id": cid, "label": _text(c.get("label"), 60, "Camera label"), "resident": res, "zone": zone}
        if ex:
            cam["exit"] = ex
        cams.append(cam)
    out["cameras"] = cams
    t = new.get("timeouts")
    if t:
        out["timeouts"] = {"l1": _num(t.get("l1"), 5, 600, "Heads-up wait"), "ack": _num(t.get("ack"), 5, 600, "Urgent wait")}  # type: ignore[union-attr]
    return out
