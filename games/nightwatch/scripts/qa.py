"""NightWatch QA gate: run before code freeze. Standard library only (runs on the Snapdragon laptop).

    python scripts/qa.py                      # full gate: own server, API flows, page checks, perf
    python scripts/qa.py --rules              # skip the ExecuTorch models (rule scorer, no camera model)
    python scripts/qa.py --only static,pairing,camera,perf      # a subset (see FLOWS below)
    python scripts/qa.py --lan-check          # also start with --lan and call it over the LAN IP (real non-local client)

It starts its OWN server (free port, short timeouts, a temp copy of the demo facility so the laptop's
saved settings are never read or written), runs every flow, always stops the server, prints a
PASS/FAIL/WARN summary and writes logs/qa/<timestamp>.md. Exit code 0 only if nothing FAILed.

Nothing here writes to shared state: settings are only POSTed to an in-process test server (403 path),
and the successful pairing runs against a temp device store, never logs/paired_devices.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
NW = ROOT / "nightwatch"
WEB = NW / "web"
L1_TIMEOUT, ACK_TIMEOUT = 4.0, 3.0
PIN = "135790"
FLOWS = ["static", "elopement", "acknowledged", "bathroom", "day_visits", "camera", "pairing", "perf"]


# ---------------------------------------------------------------------------------------------- results

class Gate:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, float]] = []   # status, name, detail, seconds
        self.notes: list[str] = []

    def add(self, status: str, name: str, detail: str = "", secs: float = 0.0) -> None:
        self.rows.append((status, name, detail, secs))
        colour = {"PASS": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m"}.get(status, "")
        reset = "\033[0m" if colour and sys.stdout.isatty() else ""
        colour = colour if reset else ""
        print(f"{colour}{status:4}{reset}  {name:<52} {secs:6.1f}s  {detail}", flush=True)

    def check(self, ok: bool, name: str, detail: str = "", secs: float = 0.0, warn: bool = False) -> bool:
        self.add("PASS" if ok else ("WARN" if warn else "FAIL"), name, detail, secs)
        return ok

    def count(self, status: str) -> int:
        return sum(1 for r in self.rows if r[0] == status)


# ---------------------------------------------------------------------------------------------- http

class Api:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base, self.token = base, token

    def call(self, path: str, body: dict | None = None, timeout: float = 10) -> tuple[int, object, float]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-NightWatch-Token"] = self.token
        req = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, headers)
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        ms = (time.perf_counter() - start) * 1000
        try:
            data: object = json.loads(raw)
        except ValueError:
            data = raw.decode("utf-8", "replace")
        return status, data, ms

    def get(self, path: str) -> dict:
        status, data, _ = self.call(path)
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"GET {path} -> {status}: {str(data)[:200]}")
        return data

    def post(self, path: str, body: dict) -> dict:
        status, data, _ = self.call(path, body)
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"POST {path} -> {status}: {str(data)[:200]}")
        return data

    def raw(self, path: str) -> tuple[int, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""


def wait_for(pred, timeout: float, every: float = 0.2):  # type: ignore[no-untyped-def]
    """Poll pred() until it returns something truthy; return (value, seconds) or (None, timeout)."""
    start = time.time()
    while time.time() - start < timeout:
        v = pred()
        if v:
            return v, time.time() - start
        time.sleep(every)
    return None, timeout


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return port if port != 8000 else free_port()


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------- server

class Server:
    def __init__(self, python: str, use_models: bool, lan: bool, logdir: Path) -> None:
        self.python, self.use_models, self.lan, self.logdir = python, use_models, lan, logdir
        self.proc: subprocess.Popen | None = None
        self.tmp = Path(tempfile.mkdtemp(prefix="nw-qa-"))
        self.port = 0
        self.log_path = logdir / "server.log"
        self.detector = False
        self.scorer = ""
        self.cmd: list[str] = []

    def _cmd(self, models: bool) -> list[str]:
        facility = self.tmp / "facility.json"
        shutil.copy(NW / "demo" / "facility.json", facility)   # never the laptop's saved settings
        cmd = [self.python, str(NW / "server.py"), "--port", str(self.port), "--l1-timeout", str(L1_TIMEOUT),
               "--ack-timeout", str(ACK_TIMEOUT), "--facility", str(facility), "--pin", PIN]
        try:
            helptext = subprocess.run([self.python, str(NW / "server.py"), "--help"], capture_output=True,
                                      text=True, timeout=60, cwd=ROOT).stdout
        except (OSError, subprocess.TimeoutExpired):
            helptext = ""
        if "--fresh" in helptext:
            cmd.append("--fresh")
        if "--state" in helptext:
            cmd += ["--state", str(self.tmp / "night_state.json")]   # never the demo's logs/night_state.json
        else:
            print("NOTE: server.py has no --state flag; if it saves state, it saves to the shared logs/ folder")
        if self.lan:
            cmd.append("--lan")
        if models:
            pm, cm = ROOT / "models" / "normal_pattern_xnnpack.pte", ROOT / "models" / "person_ssdlite_xnnpack.pte"
            if pm.exists():
                cmd += ["--model", str(pm), "--device", "CPU"]
            if cm.exists() and (ROOT / "models" / "person_ssdlite_anchors.pt").exists():
                cmd += ["--camera-model", str(cm), "--camera-device", "CPU"]
        return cmd

    def start(self, gate: Gate) -> Api:
        for models in ([True, False] if self.use_models else [False]):
            self.port = free_port()
            self.cmd = self._cmd(models)
            log = open(self.log_path, "a", encoding="utf-8")
            log.write(f"\n$ {' '.join(self.cmd)}\n")
            log.flush()
            t0 = time.time()
            self.proc = subprocess.Popen(self.cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            api = Api(f"http://127.0.0.1:{self.port}")

            def up() -> dict | None:
                if self.proc and self.proc.poll() is not None:
                    return {"dead": True}
                try:
                    return api.get("/api/health")
                except (OSError, RuntimeError):
                    try:
                        return api.get("/api/state")
                    except (OSError, RuntimeError):
                        return None

            health, secs = wait_for(up, 120, 0.5)
            if health and not health.get("dead"):
                pm = (health.get("pattern_model") or {}).get("name") or (health.get("scorer") or {}).get("name", "?")
                self.scorer = str(pm)
                self.detector = bool(health.get("camera_model"))
                gate.add("PASS", "server starts and answers /api/health", f"port {self.port}, {secs:.1f}s, "
                         f"pattern={pm}, camera={'loaded' if self.detector else 'none'}", time.time() - t0)
                if models and "ExecuTorch" not in self.scorer:
                    gate.add("WARN", "ExecuTorch pattern model loaded", f"fell back to {self.scorer}; see {self.log_path.name}")
                if models and not self.detector:
                    gate.add("WARN", "person detector loaded", "no camera model: /api/frame checks will be skipped")
                return api
            self.stop()
            gate.add("WARN" if models else "FAIL", "server starts" + (" with ExecuTorch models" if models else " (rules)"),
                     f"did not come up in {secs:.0f}s; see {self.log_path}", time.time() - t0)
        raise SystemExit(2)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None

    def rss_mb(self) -> float | None:
        if not self.proc:
            return None
        pid = self.proc.pid
        try:
            if os.name == "nt":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                     text=True, timeout=10).stdout
                kb = re.sub(r"[^\d]", "", out.strip().split('","')[-1])
                return int(kb) / 1024 if kb else None
            out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=10).stdout
            return int(out.strip()) / 1024 if out.strip() else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------------------------- API flows

def alerts_for(state: dict, rid: str, include_recent: bool = False) -> list[dict]:
    pool = list(state.get("alerts", [])) + (list(state.get("recent", [])) if include_recent else [])
    return [a for a in pool if a.get("resident") == rid]


def resident(state: dict, rid: str) -> dict:
    return next(r for r in state["residents"] if r["id"] == rid)


def history_text(alert: dict) -> str:
    return " | ".join(str(h[1]) if isinstance(h, (list, tuple)) else str(h) for h in alert.get("history", []))


def flow_elopement(api: Api, gate: Gate) -> None:
    """night_elopement with nobody answering: heads-up -> urgent -> emergency -> outside, then resolve + file."""
    t0 = time.time()
    api.post("/api/scenario", {"name": "night_elopement"})
    time.sleep(0.5)
    seen: dict[int, float] = {}
    alert: dict | None = None
    outside_at = None
    deadline = time.time() + 95
    while time.time() < deadline:
        st = api.get("/api/state")
        mine = alerts_for(st, "R-07")
        if mine:
            alert = mine[0]
            seen.setdefault(int(alert["level"]), time.time())
            if "OUTSIDE" in str(alert["title"]) and alert["level"] == 3:
                outside_at = time.time()
                break
        time.sleep(0.2)
    secs = time.time() - t0
    if not gate.check(alert is not None, "elopement: an alert is raised for R-07", "", secs):
        return
    assert alert is not None
    gate.check(1 in seen, "elopement: heads-up (level 1) appears first",
               f"first seen at +{seen[1] - t0:.1f}s" if 1 in seen else f"levels seen: {sorted(seen)}", secs)
    if 1 in seen and 2 in seen:
        d = seen[2] - seen[1]
        # urgent comes from the L1 timeout (4 s) or the front door opening ~9 s after the corridor
        gate.check(d <= 12, "elopement: heads-up -> urgent in time", f"{d:.1f}s (limit 12s: L1 timeout {L1_TIMEOUT:g}s "
                   f"or door ~9s after corridor)", secs)
    else:
        gate.add("FAIL", "elopement: heads-up -> urgent", f"levels seen: {sorted(seen)}", secs)
    if 2 in seen and 3 in seen:
        d = seen[3] - seen[2]
        gate.check(ACK_TIMEOUT - 0.6 <= d <= ACK_TIMEOUT + 2.0, "elopement: urgent -> emergency after ack timeout",
                   f"{d:.1f}s (ack timeout {ACK_TIMEOUT:g}s, slack -0.6/+2.0)", secs)
    else:
        gate.add("FAIL", "elopement: urgent -> emergency", f"levels seen: {sorted(seen)}", secs)
    h = history_text(alert)
    steps = ["All staff phones", "on-call supervisor", "Emergency services"]
    missing = [s for s in steps if s.lower() not in h.lower()]
    gate.check(not missing, "elopement: contact steps in alert history", f"missing {missing}" if missing else
               "all 3 steps (staff phones, supervisor, emergency services)", secs)
    gate.check("(simulated" in h, "elopement: contact steps say simulated (never dials)", "", secs)
    if not gate.check(outside_at is not None, "elopement: outside -> level 3 'OUTSIDE'",
                      f"title: {alert['title']!r}, level {alert['level']}", secs):
        return
    aid = int(alert["id"])
    api.post("/api/resolve", {"alert": aid, "outcome": "Found outside, walked back (QA)"})
    st = api.get("/api/state")
    done = next((a for a in st.get("recent", []) if a["id"] == aid), None)
    rec = (done or {}).get("record") or {}
    gate.check(bool(done) and rec.get("severity") == 3, "elopement: resolve -> care record severity 3",
               f"record severity={rec.get('severity')} label={rec.get('severity_label')} "
               f"location={rec.get('location_type')!r}", time.time() - t0)
    gate.check("Exterior" in str(rec.get("location_type")), "elopement: record location is 'Exterior'",
               str(rec.get("location_type")))
    tax = st.get("taxonomy") or {}
    if not gate.check(all(tax.get(k) for k in ("affect", "intervention", "nondrug_alt")), "state.taxonomy has pick-lists",
                      f"keys: {sorted(tax)}"):
        return
    status, bad, _ = api.call("/api/record", {"alert": aid, "affect": "Not a real affect", "intervention": tax["intervention"][0],
                                              "by": "QA"})
    gate.check(isinstance(bad, dict) and "error" in bad, "record: value outside taxonomy is rejected",
               f"HTTP {status}: {str(bad)[:90]}")
    body = {"alert": aid, "affect": tax["affect"][0], "intervention": tax["intervention"][0],
            "nondrug_alt": tax["nondrug_alt"][0], "note": "QA gate <b>note</b>", "by": "QA nurse"}
    status, out, _ = api.call("/api/record", body)
    filed = (out or {}).get("record", {}) if isinstance(out, dict) else {}
    gate.check(status == 200 and filed.get("status") == "filed" and filed.get("affect") == tax["affect"][0],
               "record: POST /api/record files it with taxonomy values", f"HTTP {status}, status={filed.get('status')}")
    flow_report(api, gate, {"alerts_min": 1, "reached_emergency_min": 1, "went_outside": 1, "records_filed": 1,
                            "severity3_min": 1, "open": 0})


def flow_report(api: Api, gate: Gate, expect: dict) -> None:
    rep = api.get("/api/report")
    st = api.get("/api/state")
    t = rep.get("totals", {})
    alerts = rep.get("alerts", [])
    problems = []
    if t.get("alerts") != len(alerts):
        problems.append(f"totals.alerts={t.get('alerts')} but {len(alerts)} alerts listed")
    if t.get("alerts") != st.get("stats", {}).get("alerts"):
        problems.append(f"report alerts {t.get('alerts')} != state stats {st.get('stats', {}).get('alerts')}")
    if t.get("open") != sum(1 for a in alerts if not a.get("resolved")):
        problems.append("totals.open doesn't match unresolved alerts")
    if t.get("reached_emergency") != sum(1 for a in alerts if a.get("max_level", 0) >= 3):
        problems.append("totals.reached_emergency doesn't match max_level>=3")
    if sum(int(v) for v in rep.get("severity", {}).values()) != sum(1 for a in alerts if a.get("record")):
        problems.append("severity counts != alerts with a care record")
    if t.get("records_filed", 0) + t.get("records_draft", 0) != sum(1 for a in alerts if a.get("record")):
        problems.append("records_filed + records_draft != records")
    if sum(rep.get("by_resident", {}).values()) != sum(1 for a in alerts if a.get("resident")):
        problems.append("by_resident doesn't add up")
    if "alerts_min" in expect and t.get("alerts", 0) < expect["alerts_min"]:
        problems.append(f"expected >= {expect['alerts_min']} alerts, got {t.get('alerts')}")
    for k in ("went_outside", "records_filed", "open", "visits_logged"):
        if k in expect and t.get(k) != expect[k]:
            problems.append(f"{k}: expected {expect[k]}, got {t.get(k)}")
    if "reached_emergency_min" in expect and t.get("reached_emergency", 0) < expect["reached_emergency_min"]:
        problems.append(f"reached_emergency {t.get('reached_emergency')}")
    if "severity3_min" in expect and int(rep.get("severity", {}).get("3", 0)) < expect["severity3_min"]:
        problems.append(f"severity 3 count {rep.get('severity')}")
    gate.check(not problems, "report: /api/report totals consistent", "; ".join(problems) or
               f"alerts={t.get('alerts')} emergency={t.get('reached_emergency')} outside={t.get('went_outside')} "
               f"filed={t.get('records_filed')} visits={t.get('visits_logged')}")


def flow_acknowledged(api: Api, gate: Gate) -> None:
    """Ack at urgent stops the climb; Found safe puts R-07 back in the room; no new heads-up right after."""
    t0 = time.time()
    api.post("/api/scenario", {"name": "night_elopement"})
    time.sleep(0.5)

    def urgent() -> dict | None:
        mine = alerts_for(api.get("/api/state"), "R-07")
        return mine[0] if mine and mine[0]["level"] >= 2 else None

    alert, _ = wait_for(urgent, 45, 0.15)
    if not gate.check(alert is not None, "ack: R-07 reaches urgent", "", time.time() - t0):
        return
    aid, level = int(alert["id"]), int(alert["level"])
    api.post("/api/ack", {"alert": aid, "by": "QA nurse"})
    st = api.get("/api/state")
    a = next((x for x in st["alerts"] if x["id"] == aid), {})
    gate.check(a.get("acked_by") == "QA nurse" and a.get("escalates_in_s") is None,
               "ack: acked_by set, escalation countdown cleared", f"acked at level {level}")
    contacts_before = history_text(a).count("(simulated")
    # Keep watching through the front door opening (~+23 s into the scenario), then a full ack timeout.
    wait_for(lambda: any(e["id"] == "front" and e["open"] for e in api.get("/api/state")["exits"]), 15, 0.3)
    time.sleep(ACK_TIMEOUT + 2.5)
    a = next((x for x in api.get("/api/state")["alerts"] if x["id"] == aid), {})
    gate.check(a.get("level") == level and history_text(a).count("(simulated") == contacts_before,
               "ack: no further escalation after acknowledgement",
               f"level {level} -> {a.get('level')}, contact steps {contacts_before} -> {history_text(a).count('(simulated')}",
               time.time() - t0)
    api.post("/api/resolve", {"alert": aid, "outcome": "Found safe, walked back (QA)"})
    r = resident(api.get("/api/state"), "R-07")
    gate.check(r["zone"] == "room" and r["in_bed"], "found safe: R-07 back in their room",
               f"zone={r['zone']} in_bed={r['in_bed']}")
    newer: list[dict] = []
    end = time.time() + 8
    while time.time() < end and not newer:
        newer = [x for x in alerts_for(api.get("/api/state"), "R-07") if x["id"] != aid]
        time.sleep(0.4)
    gate.check(not newer, "found safe: no new alert for R-07 right after (8 s)",
               f"new: {[x['title'] for x in newer]}" if newer else "", time.time() - t0)


def flow_bathroom(api: Api, gate: Gate) -> None:
    t0 = time.time()
    api.post("/api/scenario", {"name": "night_bathroom"})
    time.sleep(0.5)
    titles: set[str] = set()
    end = time.time() + 28
    while time.time() < end:
        st = api.get("/api/state")
        titles |= {a["title"] for a in st["alerts"] + st.get("recent", [])}
        time.sleep(0.5)
    st = api.get("/api/state")
    r = resident(st, "R-15")
    gate.check(not titles and st["stats"]["alerts"] == 0, "night_bathroom: no alert", f"alerts: {sorted(titles)}"
               if titles else f"R-15 back in bed={r['in_bed']}, peak-free", time.time() - t0)


def flow_day_visits(api: Api, gate: Gate) -> None:
    t0 = time.time()
    api.post("/api/scenario", {"name": "day_visits"})
    time.sleep(0.5)
    titles: set[str] = set()
    end = time.time() + 24
    while time.time() < end:
        st = api.get("/api/state")
        titles |= {a["title"] for a in st["alerts"] + st.get("recent", [])}
        if st["stats"]["visits_today"] >= 3 and time.time() - t0 > 21:
            break
        time.sleep(0.5)
    st = api.get("/api/state")
    gate.check(not st["night"], "day_visits: clock is in day mode", st["clock"])
    gate.check(st["stats"]["visits_today"] == 3, "day_visits: 3 door openings counted as visits",
               f"visits_today={st['stats']['visits_today']}", time.time() - t0)
    gate.check(not titles and st["stats"]["alerts"] == 0, "day_visits: no alert", f"alerts: {sorted(titles)}",
               time.time() - t0)
    flow_report(api, gate, {"visits_logged": 3, "open": 0})


def flow_camera(api: Api, gate: Gate, detector: bool) -> None:
    t0 = time.time()
    api.post("/api/scenario", {"name": "camera_walkout"})
    time.sleep(0.5)

    def left() -> dict | None:
        st = api.get("/api/state")
        return next((a for a in st["alerts"] + st.get("recent", []) if "left their room" in a["title"]), None)

    alert, secs = wait_for(left, 20, 0.3)
    gate.check(alert is not None, "camera_walkout: simulated camera raises 'left their room'",
               f"{alert['title']!r} after {secs:.1f}s" if alert else "no such alert in 20 s", time.time() - t0)
    st = api.get("/api/state")
    cam = next((c for c in st["cameras"] if c["id"] == "hall12"), st["cameras"][0] if st["cameras"] else None)
    if not gate.check(cam is not None, "camera: a camera is configured", ""):
        return
    cid = cam["id"]
    time.sleep(8)   # let the scenario's remaining frames finish so they don't race the checks below

    def cam_state() -> dict:
        return next(c for c in api.get("/api/state")["cameras"] if c["id"] == cid)

    def frame() -> tuple[int, object]:
        status, out, _ = api.call("/api/frame", {"camera": cid, "jpeg": "data:image/jpeg;base64,AAAA"})
        return status, out

    api.post("/api/camera/config", {"camera": cid, "enabled": False, "by": "QA"})
    c = cam_state()
    gate.check(not c["active"] and "off" in str(c["paused_reason"]), "camera off: state shows paused",
               f"active={c['active']} reason={c['paused_reason']!r}")
    if detector:
        status, out = frame()
        gate.check(status == 200 and isinstance(out, dict) and out.get("paused") is True,
                   "camera off: /api/frame returns paused", f"HTTP {status}: {str(out)[:100]}")
    else:
        gate.add("WARN", "camera off: /api/frame returns paused", "skipped: no person detector loaded "
                 "(server.py checks for a model before the pause)")
    api.post("/api/camera/config", {"camera": cid, "enabled": True, "schedule": "night", "by": "QA"})
    api.post("/api/clock", {"time": "14:00"})
    c = cam_state()
    gate.check(not c["active"] and "night" in str(c["paused_reason"]).lower(), "night-only during day: paused",
               f"active={c['active']} reason={c['paused_reason']!r}")
    if detector:
        status, out = frame()
        gate.check(status == 200 and isinstance(out, dict) and out.get("paused") is True,
                   "night-only during day: /api/frame returns paused", f"HTTP {status}: {str(out)[:100]}")
    api.post("/api/clock", {"time": "02:00"})
    c = cam_state()
    gate.check(c["active"], "night-only at 02:00: active again", f"reason={c['paused_reason']!r}")
    api.post("/api/camera/config", {"camera": cid, "schedule": "always", "by": "QA"})
    status, out, _ = api.call("/api/camera/config", {"camera": "no-such-cam", "enabled": False})
    gate.check(isinstance(out, dict) and "error" in out, "camera config: unknown camera is an error", f"HTTP {status}")


def flow_pairing(api: Api, gate: Gate, lan_port: int | None) -> None:
    """Laptop-side checks on the real server, then a non-local client against an in-process server."""
    st = api.get("/api/state")
    gate.check((st.get("pairing") or {}).get("pin") == PIN, "pairing: laptop (localhost) sees the PIN in /api/state")
    health = api.get("/api/health")
    gate.check((health.get("phones") or {}).get("pin") == PIN, "pairing: laptop sees the PIN in /api/health")
    status, out, _ = api.call("/api/pair", {"pin": "000000" if PIN != "000000" else "111111", "name": "QA"})
    gate.check(status == 403, "pairing: wrong PIN refused (real server)", f"HTTP {status}: {out}")

    if lan_port:
        ip = lan_ip()
        if ip:
            remote = Api(f"http://{ip}:{lan_port}")
            s1, _, _ = remote.call("/api/state")
            s2, _, _ = remote.call("/api/ack", {"alert": 1})
            s3, page = remote.raw("/staff/")
            gate.check(s1 == 401 and s2 == 401 and s3 == 200, "pairing (LAN IP): unpaired client gets 401, pages 200",
                       f"state {s1}, ack {s2}, /staff/ {s3} via {ip}")
            s4, _, _ = remote.call("/api/settings", {"facility": "x"})
            gate.check(s4 in (401, 403), "settings POST refused over the LAN (unpaired)", f"HTTP {s4}")
        else:
            gate.add("WARN", "pairing (LAN IP)", "no LAN address found")

    # Non-local client, in process: the same Handler, with is_local forced False (like tests/test_server_auth.py).
    try:
        sys.path.insert(0, str(NW))
        import server  # type: ignore  # noqa: PLC0415
        from engine import Engine  # type: ignore  # noqa: PLC0415
        from pairing import Pairing  # type: ignore  # noqa: PLC0415
        from scorer import RuleScorer  # type: ignore  # noqa: PLC0415
        from http.server import ThreadingHTTPServer  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        gate.add("WARN", "pairing: in-process non-local client", f"could not import server: {exc!r}")
        return
    tmp = Path(tempfile.mkdtemp(prefix="nw-qa-pair-"))
    original = Pairing.__dict__["is_local"]
    quiet = server.Handler.__dict__.get("log_message")
    remote_mode = {"on": True}
    httpd = None
    try:
        server.ENGINE = Engine(NW / "demo" / "facility.json", RuleScorer(), 20, 15)
        server.PAIRING = Pairing(tmp / "devices.json", pin=PIN)
        server.SETTINGS_FILE = tmp / "facility.local.json"
        Pairing.is_local = staticmethod(lambda ip: not remote_mode["on"])  # type: ignore[method-assign]
        server.Handler.log_message = lambda self, *a: None  # type: ignore[method-assign]
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        phone = Api(f"http://127.0.0.1:{httpd.server_address[1]}")
        s1, _, _ = phone.call("/api/state")
        s2, _, _ = phone.call("/api/ack", {"alert": 1})
        s3, _ = phone.raw("/staff/")
        gate.check(s1 == 401 and s2 == 401, "pairing: unpaired phone gets 401 on /api/", f"state {s1}, ack {s2}")
        gate.check(s3 == 200, "pairing: pages stay public for the PIN prompt", f"/staff/ {s3}")
        s4, out, _ = phone.call("/api/pair", {"pin": PIN, "name": "QA phone"})
        token = (out or {}).get("token") if isinstance(out, dict) else None
        gate.check(s4 == 200 and bool(token), "pairing: right PIN -> token", f"HTTP {s4}")
        phone.token = token
        s5, state, _ = phone.call("/api/state")
        gate.check(s5 == 200 and isinstance(state, dict) and "pairing" not in state,
                   "pairing: paired phone reads state, never sees the PIN", f"HTTP {s5}")
        s6, h, _ = phone.call("/api/health")
        gate.check(s6 == 200 and isinstance(h, dict) and "phones" not in h, "pairing: phone's /api/health has no PIN/address")
        s7, out7, _ = phone.call("/api/settings", {"facility": "QA"})
        gate.check(s7 == 403, "settings POST is laptop-only (paired phone -> 403)", f"HTTP {s7}: {str(out7)[:70]}")
        s8, _, _ = phone.call("/api/bench/run", {"kind": "pattern"})
        gate.check(s8 == 403, "bench run is laptop-only (paired phone -> 403)", f"HTTP {s8}")
        intruder = Api(phone.base)
        codes = [intruder.call("/api/pair", {"pin": f"{i:06d}"})[0] for i in range(6) if f"{i:06d}" != PIN]
        s9, out9, _ = intruder.call("/api/pair", {"pin": PIN})
        gate.check(s9 in (403, 429) and "too many" in str(out9), "pairing: lockout after 5 wrong PINs (even with right PIN)",
                   f"codes {codes} then {s9}: {str(out9)[:60]}")
    except Exception as exc:  # noqa: BLE001
        gate.add("FAIL", "pairing: in-process non-local client", repr(exc))
    finally:
        Pairing.is_local = original  # type: ignore[method-assign]
        if quiet is not None:
            server.Handler.log_message = quiet  # type: ignore[method-assign]
        if httpd:
            httpd.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


def flow_perf(api: Api, gate: Gate, srv: Server, calls: int) -> None:
    times = []
    for _ in range(calls):
        _, _, ms = api.call("/api/state")
        times.append(ms)
    times.sort()
    p50, p95 = times[len(times) // 2], times[min(len(times) - 1, int(len(times) * 0.95))]
    detail = f"{calls} calls: p50 {p50:.1f} ms, p95 {p95:.1f} ms, max {times[-1]:.1f} ms"
    if p95 > 250:
        gate.add("FAIL", "perf: /api/state p95", detail + " (limit 250 ms)")
    else:
        gate.check(p95 <= 50, "perf: /api/state p95", detail + (" (warn above 50 ms)" if p95 > 50 else ""), warn=True)
    rss = srv.rss_mb()
    if rss is None:
        gate.add("WARN", "perf: server memory (RSS)", "not readable on this OS")
    else:
        gate.check(rss < 1500, "perf: server memory (RSS)", f"{rss:.0f} MB" + (" (warn above 1500 MB)" if rss >= 1500 else ""),
                   warn=True)
    st = api.get("/api/state")
    sc = st.get("scorer", {})
    gate.add("PASS", "perf: scorer latency reported", f"{sc.get('name')} on {sc.get('device')}: p50 {sc.get('p50_ms')} ms, "
             f"p95 {sc.get('p95_ms')} ms")


# ---------------------------------------------------------------------------------------------- static pages

class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.head_scripts: list[str | None] = []
        self.refs: list[tuple[str, str, str]] = []           # (tag, attr, url)
        self.ids: dict[str, int] = {}
        self.controls: list[dict] = []
        self.label_for: set[str] = set()
        self.label_depth = 0
        self.inline: list[str] = []
        self._script = False
        self.pos: dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        line = self.getpos()[0]
        if tag == "head":
            self.in_head = True
        if "id" in a:
            self.ids[a["id"]] = self.ids.get(a["id"], 0) + 1
            self.pos.setdefault(a["id"], line)
        if tag == "script":
            self._script = not a.get("src")
            if self.in_head:
                self.head_scripts.append(a.get("src") or None)
            if a.get("src"):
                self.refs.append(("script", "src", a["src"]))
        elif tag == "link" and a.get("href"):
            self.refs.append(("link", "href", a["href"]))
        elif tag in ("img", "iframe", "audio", "video", "source") and a.get("src"):
            self.refs.append((tag, "src", a["src"]))
        elif tag == "a" and a.get("href"):
            self.refs.append(("a", "href", a["href"]))
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        if tag in ("input", "select", "textarea"):
            if not (tag == "input" and a.get("type", "text").lower() in ("hidden", "submit", "button", "reset", "image")):
                self.controls.append({"tag": tag, "id": a.get("id"), "name": a.get("name"), "line": line,
                                      "labelled": bool(a.get("aria-label") or a.get("aria-labelledby") or a.get("title"))
                                      or self.label_depth > 0})

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self.in_head = False
        if tag == "label":
            self.label_depth = max(0, self.label_depth - 1)
        if tag == "script":
            self._script = False

    def handle_data(self, data: str) -> None:
        if self._script:
            self.inline.append(data)


EXTERNAL = re.compile(r"""https?://([A-Za-z0-9.\-]+)""")
OK_HOSTS = ("localhost", "127.0.0.1", "www.w3.org", "w3.org")
SAFE_WRAP = re.compile(r"^\s*(NW\.)?(esc|escapeHtml|escHtml)\s*\(")
STRING_FIELD = re.compile(r"\.(name|title|label|text|description|outcome|note|by|acked_by|backup_by|confirmed_by|facility|"
                          r"room|pattern|reason|paused_reason|error|resolved|affect|intervention|behaviour|kind|zone|"
                          r"model|device|host|inputs|power_label|location_type|category|trigger)\b|\bsuggestions\b|\bhistory\b|"
                          r"\bevents?\b")


def template_literals_after(js: str, marker: re.Pattern) -> list[tuple[int, str]]:
    """Template literals (backticks) that start right after an innerHTML-style assignment."""
    out = []
    for m in marker.finditer(js):
        i = js.find("`", m.end(), m.end() + 400)
        if i < 0:
            continue
        j, depth, buf = i + 1, 0, []
        while j < len(js):
            ch = js[j]
            if ch == "\\":
                buf.append(js[j:j + 2]); j += 2; continue
            if depth == 0 and ch == "`":
                break
            if js.startswith("${", j):
                depth += 1; buf.append("${"); j += 2; continue
            if depth and ch == "}":
                depth -= 1
            buf.append(ch); j += 1
        out.append((js.count("\n", 0, m.start()) + 1, "".join(buf)))
    return out


def interpolations(tpl: str) -> list[str]:
    out, i = [], 0
    while True:
        i = tpl.find("${", i)
        if i < 0:
            return out
        j, depth = i + 2, 1
        while j < len(tpl) and depth:
            depth += {"{": 1, "}": -1}.get(tpl[j], 0)
            j += 1
        out.append(tpl[i + 2:j - 1])
        i = j


def unescaped_html(js: str, source: str | None = None) -> list[str]:
    """innerHTML-style templates that interpolate likely server strings without esc(). Lines refer to source."""
    marker = re.compile(r"(innerHTML\s*\+?=|insertAdjacentHTML\s*\([^,]+,|outerHTML\s*=)")
    found = []
    for line, tpl in template_literals_after(js, marker):
        for expr in interpolations(tpl):
            e = expr.strip()
            if SAFE_WRAP.match(e) or "esc(" in e[:12] or "`" in e:   # nested templates are checked on their own
                continue
            bare = re.sub(r"""(['"]).*?\1""", "''", e)                      # drop string literals
            bare = re.sub(r"[\w.]+\s*[!=]==?\s*\S+", "", bare)             # comparisons yield booleans
            if STRING_FIELD.search(bare) and not re.search(r"\b(length|toFixed|round|count|size)\b", bare):
                found.append((line, e))
    src = source or js
    out = []
    for line, e in found:
        at = src.find("${" + e)
        out.append(f"line {src.count(chr(10), 0, at) + 1 if at >= 0 else line}: ${{{e[:70]}}}")
    return out


def page_folders() -> list[str]:
    return sorted(p.parent.name for p in WEB.glob("*/index.html"))


def flow_static(api: Api, gate: Gate) -> None:
    expected = ["dashboard", "staff", "sim", "camera", "report", "perf"]
    folders = page_folders()
    missing = [p for p in expected if p not in folders]
    gate.check(not missing, "static: core page folders exist", f"pages: {', '.join(folders)}"
               + (f"; MISSING {missing}" if missing else ""))
    fetched: dict[str, tuple[int, str]] = {}

    def fetch(url: str) -> tuple[int, str]:
        if url not in fetched:
            fetched[url] = api.raw(url)
        return fetched[url]

    s, _ = fetch("/")
    gate.check(s == 200, "static: / redirects to a page", f"HTTP {s}")
    for page in folders:
        t0 = time.time()
        status, html = fetch(f"/{page}/")
        if not gate.check(status == 200, f"static {page}: HTTP 200", f"HTTP {status}"):
            continue
        p = PageParser()
        p.feed(html)
        base = f"http://x/{page}/"
        broken, external, links_out = [], [], []
        texts = ["\n".join(p.inline)]
        for tag, attr, ref in p.refs:
            u = urlparse(ref)
            if u.scheme in ("http", "https") and u.hostname not in OK_HOSTS:
                (links_out if tag == "a" else external).append(ref)
                continue
            if u.scheme in ("mailto", "tel", "data", "javascript") or ref.startswith("#") or tag == "a":
                continue
            path = urlparse(urljoin(base, ref)).path
            st, body = fetch(path)
            if st != 200:
                broken.append(f"{ref} -> {st}")
            elif tag == "script" or path.endswith(".css"):
                texts.append(body)
        gate.check(not broken, f"static {page}: <script src>/<link href> resolve", "; ".join(broken))
        first = p.head_scripts[0] if p.head_scripts else None
        gate.check(bool(first) and first.endswith("auth.js"), f"static {page}: auth.js is the first <head> script",
                   f"first head script: {first}")
        dups = [f"#{k} x{v} (line {p.pos[k]})" for k, v in p.ids.items() if v > 1]
        gate.check(not dups, f"static {page}: no duplicate ids", "; ".join(dups))
        unl = [f"<{c['tag']} id={c['id']} name={c['name']}> line {c['line']}" for c in p.controls
               if not c["labelled"] and not (c["id"] and c["id"] in p.label_for)]
        gate.check(not unl, f"static {page}: form controls labelled", "; ".join(unl))
        for text in texts + [html]:
            for m in EXTERNAL.finditer(text):
                host = m.group(1).lower()
                if host not in OK_HOSTS and not any(m.group(0) in x for x in links_out):
                    ctx = text[max(0, m.start() - 40):m.start()]
                    # loads (src/href/url()/@import/fetch) fail; other mentions (e.g. strings) warn
                    if re.search(r"""(src|href)\s*=\s*["']?$|url\(\s*["']?$|@import\s+["']?$|fetch\(\s*["'`]?$""", ctx):
                        external.append(m.group(0))
                    else:
                        links_out.append(m.group(0))
        external = sorted(set(external))
        gate.check(not external, f"static {page}: no external resources (offline)", "; ".join(external))
        if links_out:
            gate.add("WARN", f"static {page}: external URLs mentioned (won't open offline)", "; ".join(sorted(set(links_out)))[:200])
        own = "\n".join(p.inline)
        risky = unescaped_html(own, html)
        if risky:
            gate.add("WARN", f"static {page}: innerHTML interpolates state fields without esc()",
                     f"{len(risky)} spot(s), first: {risky[0]}", time.time() - t0)
            gate.notes.append(f"### {page}: innerHTML interpolations without esc()\n" + "\n".join(f"- `{r}`" for r in risky))
    for asset in sorted(WEB.glob("assets/*.js")):
        risky = unescaped_html(asset.read_text(encoding="utf-8"))
        if risky:
            gate.add("WARN", f"static assets/{asset.name}: innerHTML without esc()", f"{len(risky)} spot(s), first: {risky[0]}")
            gate.notes.append(f"### assets/{asset.name}: innerHTML interpolations without esc()\n"
                              + "\n".join(f"- `{r}`" for r in risky))


# ---------------------------------------------------------------------------------------------- main

def write_report(gate: Gate, srv: Server, started: str, path: Path, total_s: float) -> None:
    lines = [f"# NightWatch QA gate: {started}", "",
             f"**{'PASSED' if gate.count('FAIL') == 0 else 'FAILED'}**: {gate.count('PASS')} pass, "
             f"{gate.count('FAIL')} fail, {gate.count('WARN')} warn, {total_s:.0f} s", "",
             f"- Server: `{' '.join(Path(c).name if os.sep in c else c for c in srv.cmd)}`",
             f"- Pattern model: {srv.scorer}; person detector: {'loaded' if srv.detector else 'none'}",
             f"- Python: {sys.version.split()[0]} on {sys.platform}", "",
             "| Result | Check | Time | Detail |", "|---|---|---:|---|"]
    for status, name, detail, secs in gate.rows:
        lines.append(f"| {status} | {name} | {secs:.1f}s | {detail.replace('|', '/')} |")
    fails = [r for r in gate.rows if r[0] == "FAIL"]
    if fails:
        lines += ["", "## Failures", *[f"- **{n}**: {d}" for _, n, d, _ in fails]]
    if gate.notes:
        lines += ["", "## Notes", *gate.notes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--python", default=sys.executable, help="interpreter for the server (needs executorch for the models)")
    ap.add_argument("--rules", action="store_true", help="don't load the ExecuTorch models")
    ap.add_argument("--only", help=f"comma-separated subset of: {','.join(FLOWS)}")
    ap.add_argument("--lan-check", action="store_true", help="start with --lan and call the API over the LAN IP")
    ap.add_argument("--perf-calls", type=int, default=200)
    args = ap.parse_args(argv)
    flows = [f.strip() for f in args.only.split(",")] if args.only else FLOWS
    unknown = set(flows) - set(FLOWS)
    if unknown:
        ap.error(f"unknown flow(s) {sorted(unknown)}; choose from {FLOWS}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = ROOT / "logs" / "qa"
    outdir.mkdir(parents=True, exist_ok=True)
    report = outdir / f"{stamp}.md"
    gate = Gate()
    srv = Server(args.python, not args.rules, args.lan_check, outdir)
    srv.log_path = outdir / f"{stamp}-server.log"
    t_start = time.time()
    print(f"NightWatch QA gate  ({', '.join(flows)})\n")
    try:
        api = srv.start(gate)
        runners = {
            "static": lambda: flow_static(api, gate),
            "elopement": lambda: flow_elopement(api, gate),
            "acknowledged": lambda: flow_acknowledged(api, gate),
            "bathroom": lambda: flow_bathroom(api, gate),
            "day_visits": lambda: flow_day_visits(api, gate),
            "camera": lambda: flow_camera(api, gate, srv.detector),
            "pairing": lambda: flow_pairing(api, gate, srv.port if args.lan_check else None),
            "perf": lambda: flow_perf(api, gate, srv, args.perf_calls),
        }
        for name in flows:
            try:
                runners[name]()
            except Exception as exc:  # noqa: BLE001 - one broken flow must not hide the others
                gate.add("FAIL", f"{name}: flow crashed", f"{type(exc).__name__}: {exc}"[:300])
            if srv.proc is None or srv.proc.poll() is not None:
                gate.add("FAIL", "server still running", f"server exited during {name}; see {srv.log_path}")
                break
        api.call("/api/scenario", {"name": "reset"})
    except SystemExit:
        pass
    except KeyboardInterrupt:
        gate.add("FAIL", "interrupted", "Ctrl+C")
    finally:
        srv.cleanup()
    total = time.time() - t_start
    write_report(gate, srv, stamp, report, total)
    ok = gate.count("FAIL") == 0
    print(f"\n{'PASSED' if ok else 'FAILED'}: {gate.count('PASS')} pass, {gate.count('FAIL')} fail, "
          f"{gate.count('WARN')} warn in {total:.0f}s")
    print(f"Report: {report.relative_to(ROOT)}   Server log: {srv.log_path.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
