"""NightWatch local server. Standard library only (plus ExecuTorch if a model is loaded).

    python nightwatch/server.py --model models/normal_pattern_qnn.pte --device NPU      # on the Copilot+ PC
    python nightwatch/server.py --model models/normal_pattern_xnnpack.pte --device CPU
    python nightwatch/server.py                                                          # rule fallback
    add --lan to let staff phones on the facility Wi-Fi open /staff/ (still never leaves the building)
    add --fresh to start a clean night instead of restoring tonight's saved state

Logs: one JSON object per line in logs/nightwatch.log (rotated at 5 MB): requests, engine events,
errors with tracebacks, start/stop. Ctrl+C (or SIGTERM) saves tonight's state before exiting.

Restart-safe: alerts (history, care records, acks), events, visits, camera on/off/schedule and doorway
lines and the demo clock are saved to logs/night_state.json (git-ignored) about 1 s after every change
and restored on the next start if the file is from the same night. Scenarios and settings reset it.

Pages:  /dashboard/  (nurse station)   /staff/  (night staff phone)   /sim/  (sensor simulator for demos)

API (JSON):
    POST /api/pair      {"pin": "123456", "name": "Night nurse phone"} -> {"token"}   (other devices must pair;
                        then send header X-NightWatch-Token on every /api/ call. The laptop itself is trusted.)
    GET  /api/state
    GET  /api/events    Server-Sent Events (text/event-stream), so pages can stop polling /api/state:
                        "event: state" with {"rev", "clock", "night", "open_alerts", "top_level", "unacked",
                        "alerts", "last_event"} whenever anything changes (at most ~4 a second), or the
                        full /api/state view plus "rev" with ?full=1; "event: heartbeat" every 10 s when
                        quiet. EventSource can't send headers, so phones pass ?token=<pairing token>.
                            const es = new EventSource('/api/events?token=' + encodeURIComponent(token));
                            es.addEventListener('state', (e) => { const s = JSON.parse(e.data); ... });
                        If the stream drops, EventSource reconnects by itself (retry 2 s); keep a slow
                        /api/state poll as a fallback. Each open stream holds one connection (max 32).
    GET  /api/bench     benchmark runs from nightwatch/bench/benchmark.py (newest first) + the running job
    POST /api/bench/run {"kind": "detector"|"pattern", "seconds": 10}  start one (laptop only)
    GET  /api/resident?id=R-07   profile, now, last 30 min history, tonight's alerts
    GET  /api/settings  /  POST /api/settings (laptop only; saved to git-ignored data/facility.local.json)
    GET  /api/health    models, cameras, uptime, "version" (git short hash), "state" (restored or not);
                        plus phone PIN/address for the laptop itself
    POST /api/record    {"alert", "affect", "intervention", "nondrug_alt"?, "note"?, "by"}  file a care record
    POST /api/backup    {"alert", "by"}  ask for help: all staff phones alerted
    GET  /api/report    the night's shift report (every alert, response times, severity, draft care records)
    POST /api/sensor    {"type": "door", "exit": "front", "open": true}
                        {"type": "bed" | "hr" | "motion" | "location", "resident": "R-07", ...}
    POST /api/ack       {"alert": 3, "by": "Night nurse"}
    POST /api/resolve   {"alert": 3, "outcome": "Found safe, walked back"}
    POST /api/clock     {"time": "02:08", "speed": 1}
    POST /api/frame     {"camera": "hall12", "jpeg": "<base64>"}   (analysed in memory, never stored)
    POST /api/camera/config {"camera": "hall12", "line": [x1, y1, x2, y2] | null, "inside": 1 | -1}
                        or {"camera": "hall12", "enabled": true|false, "schedule": "always"|"night", "by": "..."}
    POST /api/scenario  {"name": "night_elopement" | "night_bathroom" | "day_visits" | "reset"}
    GET  /api/contacts  {"live", "twilio_configured", "contacts": [{id, role, name, phone, channel, residents}],
                        "roles", "channels", "limits"}; phones see numbers masked ("+1 •••• 4821")
    POST /api/contacts  {"contacts": [...]} and/or {"live": true|false}   (laptop only; saved to git-ignored
                        data/contacts.local.json; "live" is never saved: every start is a dry run)
    POST /api/notify    {"alert": 3, "by": "..."}  "Call for help": fan out to the contact list now (once per 60 s
                        per alert). Also automatic when an alert first reaches level 3. Results land in the
                        alert's "notifications" [{contact_id, role, name, channel, status, detail, at}] and history.
                        Channels: call, sms (Twilio: TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM), push (free ntfy:
                        contact "topic"), imessage (free, macOS Messages: contact "imessage" or phone), or
                        combos like "push+imessage". Nothing real goes out until live is on; emergency
                        services are always simulated. See nightwatch/notify.py.
    POST /api/contacts/test {"contact_id": "owner"}  (laptop only) one-off "This is a NightWatch demo test
                        message." to that contact; with live off it returns what would be sent (status dry_run)

Errors: every error body is {"error": "<message staff can read>"} (plus e.g. "choices"). POST bodies must
be a JSON object sent with Content-Type: application/json (415 otherwise), at most 64 KB (2 MB for
/api/frame; 413). 400 bad field (type/range/length), 401 not paired, 403 laptop only / wrong PIN,
404 unknown resident/exit/camera/alert/scenario/route, 409 alert already resolved or still open,
429 too many wrong PINs, 503 no camera model loaded. Unexpected failures are 500 JSON, never a dropped call.
"""
from __future__ import annotations

import argparse
import base64
import functools
import json
import logging
import logging.handlers
import re
import signal
import socket
import subprocess
from urllib.parse import parse_qs, urlparse
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from engine import Engine  # noqa: E402
from notify import Notifier  # noqa: E402
from pairing import Pairing  # noqa: E402
from scorer import build  # noqa: E402
from vision.tracker import Tracker  # noqa: E402

STATE_FILE = HERE.parent / "logs" / "night_state.json"   # git-ignored
LOG_FILE = HERE.parent / "logs" / "nightwatch.log"       # git-ignored
LOG = logging.getLogger("nightwatch")
STATE_INFO: dict[str, object] = {"file": STATE_FILE.name, "restored": False, "why": "not loaded"}
ENGINE: Engine
DETECTOR: object | None = None
TRACKERS: dict[str, Tracker] = {}
PAIRING: Pairing
# Locking (no deadlocks): ENGINE.lock is an RLock guarding all engine state; every engine method takes it.
# TRACK_LOCK guards TRACKERS and may be held while taking ENGINE.lock, never the other way round.
# DETECT_LOCK serialises the detector (one ExecuTorch method instance is not safe to run concurrently)
# and is never held with the other two. BENCH_LOCK only guards BENCH_JOB's check-and-start.
TRACK_LOCK = threading.Lock()
DETECT_LOCK = threading.Lock()
BENCH_LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(HERE / "web"), **kwargs)  # type: ignore[arg-type]

    def log_message(self, fmt: str, *args: object) -> None:
        if self.path.startswith(("/api/state", "/api/events")):
            return
        msg = re.sub(r"token=[^&\s\"]+", "token=REDACTED", fmt % args)
        sys.stderr.write(f"{self.address_string()} - - [{self.log_date_time_string()}] {msg}\n")
        if args and isinstance(args[0], str) and args[0].startswith(("GET", "POST", "HEAD")):
            if args[0].startswith(("GET", "HEAD")) and len(args) > 1 and str(args[1]) in ("200", "302", "304"):
                return   # page loads and polling: not worth a line each; every POST and every error is logged
            LOG.info("http", extra={"fields": {"ip": self.client_address[0], "request": msg.split('"')[1] if '"' in msg else msg,
                                               "status": str(args[1]) if len(args) > 1 else None}})
        else:
            LOG.warning("http", extra={"fields": {"ip": self.client_address[0], "detail": msg}})

    def _authorized(self, query_token: str | None = None) -> bool:
        """The laptop is trusted; other devices need a paired token (see pairing.py)."""
        if PAIRING.allowed(self.client_address[0], self.headers.get("X-NightWatch-Token") or query_token):
            return True
        self._json({"error": "pair this device first", "pair": True}, 401)
        return False

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._get()
        except ApiError as exc:
            self._json(exc.body, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - answer 500, keep serving
            self._fail(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._post()
        except ApiError as exc:
            self._json(exc.body, exc.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 - answer 500, keep serving
            self._fail(exc)

    def _fail(self, exc: BaseException) -> None:
        import traceback

        print(f"[server] {self.command} {self.path.split('?')[0]} failed: {exc!r}\n{traceback.format_exc()}", file=sys.stderr)
        LOG.error("request failed", exc_info=exc, extra={"fields": {"method": self.command, "path": self.path.split("?")[0]}})
        try:
            self._json({"error": f"server error: {type(exc).__name__}"}, 500)
        except OSError:
            pass

    def _get(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        # Only the event stream takes the token in the URL (EventSource can't set headers).
        token = query.get("token", [None])[0] if url.path == "/api/events" else None
        if url.path.startswith("/api/") and not self._authorized(token):
            return None
        if url.path == "/api/events":
            return self._events(query.get("full", ["0"])[0] in ("1", "true"))
        if url.path == "/api/resident":
            rid = parse_qs(url.query).get("id", [""])[0]
            if not rid:
                raise ApiError(400, "id is required, e.g. /api/resident?id=R-07")
            return self._json(_checked(ENGINE.resident_detail(rid)))
        if url.path == "/api/settings":
            return self._json(ENGINE.settings())
        if url.path == "/api/contacts":
            return self._json(_notifier().view(PAIRING.is_local(self.client_address[0])))
        if url.path == "/api/health":
            return self._json(_health(PAIRING.is_local(self.client_address[0])))
        if url.path == "/api/state":
            view = ENGINE.view()
            if PAIRING.is_local(self.client_address[0]):
                # Only the laptop itself sees the PIN, to show it on the nurse-station screen.
                view["pairing"] = {"pin": PAIRING.pin, "devices": PAIRING.devices}
            return self._json(view)
        if url.path == "/api/report":
            return self._json(ENGINE.report())
        if url.path == "/api/bench":
            return self._json(_bench_runs() | {"job": {k: v for k, v in BENCH_JOB.items() if k != "cmd"}})
        if url.path.startswith("/api/"):
            raise ApiError(404, f"no such API route: GET {url.path}")
        if self.path in ("", "/"):
            self.send_response(302)
            self.send_header("Location", "/home/" if (HERE / "web" / "home" / "index.html").exists() else "/dashboard/")
            self.end_headers()
            return None
        return super().do_GET()

    def _events(self, full: bool) -> None:
        """Server-Sent Events: push a ping (or the full view) on every state change, heartbeats when quiet."""
        with SSE_LOCK:
            if SSE["clients"] >= SSE_MAX_CLIENTS:
                raise ApiError(503, "too many live event streams; poll /api/state instead")
            SSE["clients"] += 1
        local = PAIRING.is_local(self.client_address[0])
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(b"retry: 2000\n\n")
            rev, beat = -1, time.time()
            while not STOPPING.is_set():
                new = ENGINE.wait_change(rev, timeout=0.5)
                if new != rev:
                    rev = new
                    if full:
                        data = ENGINE.view()
                        if local:
                            data["pairing"] = {"pin": PAIRING.pin, "devices": PAIRING.devices}
                    else:
                        data = ENGINE.summary()
                    data["rev"] = rev
                    self._sse("state", data, rev)
                    beat = time.time()
                    STOPPING.wait(SSE_MIN_GAP_S)   # coalesce bursts of changes
                elif time.time() - beat >= SSE_HEARTBEAT_S:
                    self._sse("heartbeat", {"rev": rev, "clock": ENGINE.stamp()})
                    beat = time.time()
        except OSError:   # the page went away (BrokenPipe / ConnectionReset)
            pass
        finally:
            with SSE_LOCK:
                SSE["clients"] -= 1

    def _sse(self, event: str, data: object, rev: int | None = None) -> None:
        head = f"id: {rev}\n" if rev is not None else ""
        self.wfile.write(f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode())
        self.wfile.flush()

    def _read_body(self, path: str) -> dict[str, object]:
        """JSON object bodies only, size-capped (2 MB for camera frames, 64 KB otherwise)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad Content-Length") from None
        cap = MAX_FRAME_BODY if path == "/api/frame" else MAX_BODY
        if length < 0:
            raise ApiError(400, "bad Content-Length")
        if length > cap:
            self.close_connection = True
            if length <= 16 * 1024 * 1024:   # drain it, so the client reads our 413 instead of a broken pipe
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(left, 65536))
                    if not chunk:
                        break
                    left -= len(chunk)
            raise ApiError(413, f"request body too large ({length} bytes, limit {cap})")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            # Also what stops another web page in the laptop's browser from posting here: a cross-site
            # application/json request needs a CORS preflight, which this server never grants.
            raise ApiError(415, "send JSON with Content-Type: application/json")
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad json") from None
        if not isinstance(body, dict):
            raise ApiError(400, "the body must be a JSON object")
        return body

    def _post(self) -> None:
        path = urlparse(self.path).path
        body = self._read_body(path)
        if path == "/api/pair":
            pin, name = v_str(body, "pin", 12, required=True), v_str(body, "name", 40, default="phone") or "phone"
            ok, result = PAIRING.pair(self.client_address[0], pin, name)
            if ok:
                ENGINE.log("pairing", f"New device paired: {name}")
                return self._json({"token": result})
            return self._json({"error": result}, 429 if result.startswith("too many") else 403)
        if not self._authorized():
            return None
        route = ROUTES.get(path)
        if route is None:
            raise ApiError(404, f"no such API route: POST {path}")
        if path in LAPTOP_ONLY and not PAIRING.is_local(self.client_address[0]):
            raise ApiError(403, LAPTOP_ONLY[path])
        return self._json(route(body))

    def _json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


STOPPING = threading.Event()          # set on shutdown: event streams end
SSE_LOCK = threading.Lock()
SSE: dict[str, int] = {"clients": 0}
SSE_MAX_CLIENTS = 32
SSE_HEARTBEAT_S = 10.0
SSE_MIN_GAP_S = 0.25
MAX_BODY = 64 * 1024
MAX_FRAME_BODY = 2 * 1024 * 1024
IDENT = re.compile(r"^[A-Za-z0-9_.-]{1,24}$")
_MISSING = object()


class ApiError(Exception):
    """An error with an HTTP status; the body always has "error" (what pages already read)."""

    def __init__(self, status: int, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status, self.body = status, {"error": message, **extra}


def _get(body: dict[str, object], key: str, required: bool, default: object) -> object:
    v = body.get(key, _MISSING)
    if v is _MISSING or (v is None and not required):
        if required:
            raise ApiError(400, f"{key} is required")
        return default
    return v


def v_int(body: dict[str, object], key: str, lo: int = 1, hi: int = 10**9, required: bool = True,
          default: int | None = None) -> int | None:
    v = _get(body, key, required, default)
    if v is default and not required:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ApiError(400, f"{key} must be a whole number")
    try:
        f = float(v)
    except ValueError:
        raise ApiError(400, f"{key} must be a whole number") from None
    if f != f or f != int(f):
        raise ApiError(400, f"{key} must be a whole number")
    if not lo <= f <= hi:
        raise ApiError(400, f"{key} must be between {lo} and {hi}")
    return int(f)


def v_num(body: dict[str, object], key: str, lo: float, hi: float, required: bool = True,
          default: float | None = None) -> float | None:
    v = _get(body, key, required, default)
    if v is default and not required:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ApiError(400, f"{key} must be a number")
    try:
        f = float(v)
    except ValueError:
        raise ApiError(400, f"{key} must be a number") from None
    if not lo <= f <= hi:   # also rejects NaN
        raise ApiError(400, f"{key} must be between {lo:g} and {hi:g}")
    return f


def v_str(body: dict[str, object], key: str, maxlen: int, required: bool = False, default: str = "") -> str:
    v = _get(body, key, required, default)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str):
        raise ApiError(400, f"{key} must be text")
    v = v.strip()
    if required and not v:
        raise ApiError(400, f"{key} is required")
    if len(v) > maxlen:
        raise ApiError(400, f"{key} is longer than {maxlen} characters")
    return v


def v_by(body: dict[str, object], default: str) -> str:
    """Who did it: shown in the log and care record. Long names are cut to 40 characters, not refused."""
    return v_str(body, "by", 200, default=default)[:40] or default


def v_id(body: dict[str, object], key: str, required: bool = True, default: str | None = None) -> str | None:
    v = _get(body, key, required, default)
    if v is default and not required:
        return default
    if not isinstance(v, str) or not IDENT.match(v):
        raise ApiError(400, f"{key} must be an id (1-24 letters, digits, - _ .)")
    return v


def v_bool(body: dict[str, object], key: str, required: bool = True, default: bool | None = None) -> bool | None:
    v = _get(body, key, required, default)
    if v is default and not required:
        return default
    if isinstance(v, bool):
        return v
    if v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    raise ApiError(400, f"{key} must be true or false")


def v_choice(body: dict[str, object], key: str, choices: tuple[str, ...], required: bool = True,
             default: str | None = None) -> str | None:
    v = _get(body, key, required, default)
    if v is default and not required:
        return default
    if v not in choices:
        raise ApiError(400, f"{key} must be one of {', '.join(choices)}")
    return v  # type: ignore[return-value]


def v_numbers(body: dict[str, object], key: str, n: int, lo: float, hi: float) -> list[float]:
    v = body.get(key)
    if not isinstance(v, list) or len(v) != n:
        raise ApiError(400, f"{key} must be a list of {n} numbers")
    return [v_num({key: x}, key, lo, hi) for x in v]  # type: ignore[misc]


def _checked(out: dict[str, object]) -> dict[str, object]:
    """Engine results carry {"error": ...}; turn them into consistent HTTP statuses."""
    err = out.get("error") if isinstance(out, dict) else None
    if not err:
        return out
    msg = str(err)
    low = msg.lower()
    status = 404 if low.startswith(("unknown", "no such")) else 409 if ("already" in low or "still open" in low) else 400
    raise ApiError(status, msg, **{k: v for k, v in out.items() if k != "error"})


SENSOR_TYPES = ("door", "bed", "hr", "motion", "location")


def r_sensor(body: dict[str, object]) -> dict[str, object]:
    kind = v_choice(body, "type", SENSOR_TYPES)
    if kind == "door":
        ev: dict[str, object] = {"type": "door", "exit": v_id(body, "exit"), "open": v_bool(body, "open", False, True)}
    else:
        ev = {"type": kind, "resident": v_id(body, "resident")}
        if kind == "bed":
            ev["in_bed"] = v_bool(body, "in_bed")
        elif kind == "hr":
            ev["bpm"] = v_num(body, "bpm", 20, 250)
        elif kind == "motion":
            ev["level"] = v_num(body, "level", 0, 1)
        else:
            ev["zone"] = v_id(body, "zone")
            ex = v_id(body, "exit", required=False)
            if ex:
                ev["exit"] = ex
    return _checked(ENGINE.sensor(ev))


def r_ack(body: dict[str, object]) -> dict[str, object]:
    return _checked(ENGINE.ack(v_int(body, "alert"), v_by(body, "Staff")))  # type: ignore[arg-type]


def r_resolve(body: dict[str, object]) -> dict[str, object]:
    return _checked(ENGINE.resolve(v_int(body, "alert"), v_str(body, "outcome", 200, default="Resolved") or "Resolved"))  # type: ignore[arg-type]


def r_clock(body: dict[str, object]) -> dict[str, object]:
    t = v_str(body, "time", 5, required=True)
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ApiError(400, "time must be HH:MM, e.g. 02:08")
    ENGINE.set_time(t, v_num(body, "speed", 0.1, 3600, required=False))
    return {"ok": True}


def r_scenario(body: dict[str, object]) -> dict[str, object]:
    return _checked(ENGINE.run_scenario(v_str(body, "name", 40, required=True)))


def r_record(body: dict[str, object]) -> dict[str, object]:
    alt = v_str(body, "nondrug_alt", 60) or None
    return _checked(ENGINE.confirm_record(v_int(body, "alert"), v_str(body, "affect", 60), v_str(body, "intervention", 60),  # type: ignore[arg-type]
                                          alt, v_str(body, "note", 500), v_by(body, "Staff")))


def r_backup(body: dict[str, object]) -> dict[str, object]:
    return _checked(ENGINE.request_backup(v_int(body, "alert"), v_by(body, "Staff")))  # type: ignore[arg-type]


def r_settings(body: dict[str, object]) -> dict[str, object]:
    return _checked(ENGINE.apply_settings(body, SETTINGS_FILE))


def r_bench(body: dict[str, object]) -> dict[str, object]:
    clean = {"kind": v_choice(body, "kind", ("detector", "pattern"), required=False, default="pattern"),
             "seconds": v_num(body, "seconds", 1, 600, required=False, default=10)}
    out = _bench_run(clean)
    if out.get("error"):
        raise ApiError(409 if "already" in str(out["error"]) else 503, str(out["error"]))
    return out


def r_frame(body: dict[str, object]) -> dict[str, object]:
    cam = v_id(body, "camera", required=False, default="hall12")
    v_str(body, "jpeg", MAX_FRAME_BODY, required=True)
    if body.get("roi") is not None:
        x0, y0, size = v_numbers(body, "roi", 3, -1, 2)
        if size <= 0:
            raise ApiError(400, "roi size must be above 0")
    if DETECTOR is None:
        raise ApiError(503, "no camera model loaded: start the server with --camera-model")
    with ENGINE.lock:
        if cam not in ENGINE.cameras:
            raise ApiError(404, f"unknown camera {cam!r}")
    return _frame({**body, "camera": cam})


def r_camera_config(body: dict[str, object]) -> dict[str, object]:
    cam = v_id(body, "camera", required=False, default="hall12")
    with ENGINE.lock:
        if cam not in ENGINE.cameras:
            raise ApiError(404, f"unknown camera {cam!r}")
    clean: dict[str, object] = {"camera": cam, "by": v_by(body, "staff")}
    if "enabled" in body:
        clean["enabled"] = v_bool(body, "enabled")
    if "schedule" in body:
        clean["schedule"] = v_choice(body, "schedule", ("always", "night"))
    if "line" in body or not ("enabled" in clean or "schedule" in clean):
        line = None if body.get("line") is None else v_numbers(body, "line", 4, -1, 2)
        if line and line[:2] == line[2:]:
            raise ApiError(400, "line needs two different points")
        clean["line"] = line
        clean["inside"] = v_int(body, "inside", -1, 1, required=False, default=1)
        if clean["inside"] == 0:
            raise ApiError(400, "inside must be 1 or -1")
    return _checked(_camera_config(clean))


def _notifier() -> Notifier:
    if ENGINE.notifier is None:
        raise ApiError(503, "contacts are not set up on this server")
    return ENGINE.notifier


def r_contacts(body: dict[str, object]) -> dict[str, object]:
    n = _notifier()
    live = v_bool(body, "live", required=False)
    if "contacts" not in body and live is None:
        raise ApiError(400, "send contacts and/or live")
    try:
        n.update(body.get("contacts") if "contacts" in body else None, live)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from None
    if "contacts" in body:
        ENGINE.log("settings", f"Contact list saved ({len(n.contacts)} contacts)")
    if live is not None:
        ENGINE.log("settings", "Live contact calls/SMS turned " + ("ON (real calls and texts can go out)" if live else "OFF (dry run)")
                   + " on the nurse-station laptop")
    return n.view(local=True)


def r_contacts_test(body: dict[str, object]) -> dict[str, object]:
    _notifier()
    out = _checked(ENGINE.test_contact(v_id(body, "contact_id")))  # type: ignore[arg-type]
    out.pop("masked", None)
    return out


def r_notify(body: dict[str, object]) -> dict[str, object]:
    return _checked(ENGINE.notify(v_int(body, "alert"), v_by(body, "Staff")))  # type: ignore[arg-type]


ROUTES = {"/api/contacts": r_contacts, "/api/contacts/test": r_contacts_test, "/api/notify": r_notify, "/api/sensor": r_sensor, "/api/ack": r_ack, "/api/resolve": r_resolve, "/api/clock": r_clock,
          "/api/frame": r_frame, "/api/camera/config": r_camera_config, "/api/scenario": r_scenario,
          "/api/record": r_record, "/api/bench/run": r_bench, "/api/backup": r_backup, "/api/settings": r_settings}
LAPTOP_ONLY = {"/api/contacts": "the contact list can only be changed on the nurse-station laptop",
               "/api/contacts/test": "test messages can only be sent from the nurse-station laptop",
               "/api/bench/run": "benchmarks can only be started on the laptop",
               "/api/settings": "settings can only be changed on the nurse-station laptop"}


BENCH_KEYS = ("label", "pte", "pte_mb", "load_rss_mb", "calls", "per_s", "p50_ms", "p95_ms", "p99_ms",
              "watts_running", "watts_idle", "watts_above_idle", "mj_per_frame", "mj_per_check", "output_check")


def _bench_runs(limit: int = 12) -> dict[str, object]:
    """Benchmark results written by nightwatch/bench/benchmark.py (newest first), without local file paths."""
    runs = []
    for path in sorted((HERE.parent / "logs" / "bench").glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        host = d.get("host", {})
        runs.append({"timestamp": d.get("timestamp"), "kind": d.get("kind"), "inputs": d.get("inputs"),
                     "rate": d.get("requested_rate_per_s"), "power_label": d.get("power_label"),
                     "on_ac": d.get("power_on_ac"),
                     "host": {k: host.get(k) for k in ("system", "machine", "executorch")},
                     "results": [{k: r.get(k) for k in BENCH_KEYS if k in r} for r in d.get("results", [])],
                     "notes": d.get("notes", [])})
    return {"runs": runs}


SETTINGS_FILE = HERE.parent / "data" / "facility.local.json"   # git-ignored
STARTED = time.time()
LAN = {"on": False, "port": 8000}


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # no packet is sent; picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return None


def _health(local: bool) -> dict[str, object]:
    """What's running: for the home page. The PIN and phone address only go to the laptop itself."""
    with ENGINE.lock:
        out = _health_unlocked()
    if local:
        ip = _lan_ip() if LAN["on"] else None
        out["phones"] = {"pin": PAIRING.pin, "paired": PAIRING.devices,
                         "url": f"http://{ip}:{LAN['port']}/staff/" if ip else None}
    return out


@functools.lru_cache(maxsize=1)
def _version() -> str | None:
    """Git short hash of the running code (None outside a git checkout)."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE.parent, capture_output=True,
                             text=True, timeout=3)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _health_unlocked() -> dict[str, object]:
    return {
        "version": _version(), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(STARTED)),
        "state": dict(STATE_INFO),
        "uptime_s": round(time.time() - STARTED),
        "pattern_model": {"name": ENGINE.scorer.name, "device": ENGINE.scorer.device},  # type: ignore[attr-defined]
        "camera_model": {"name": DETECTOR.name, "device": DETECTOR.device} if DETECTOR else None,  # type: ignore[attr-defined]
        "cameras": [{"id": c["id"], "label": c["label"], "enabled": c["enabled"], "schedule": c["schedule"],
                     "live": time.time() - c["last_frame"] < 3} for c in ENGINE.cameras.values()],
        "lan": LAN["on"], "facility": ENGINE.facility, "settings_file": ENGINE.facility_path.name,
        "tests": "python -m pytest -q tests",
    }


BENCH_JOB: dict[str, object] = {"running": False, "kind": None, "started": None, "log": "", "error": None}


def _bench_pairs(kind: str) -> list[str]:
    """NPU (QNN) and CPU (XNNPACK) builds of one model, whichever exist on this laptop."""
    models = HERE.parent / "models"
    if kind == "detector":
        npu = next((p for p in ("person_ssdlite_qnn_int8.pte", "person_ssdlite_qnn.pte") if (models / p).exists()), None)
        cpu = "person_ssdlite_xnnpack.pte"
    else:
        npu = "normal_pattern_qnn.pte" if (models / "normal_pattern_qnn.pte").exists() else None
        cpu = "normal_pattern_xnnpack.pte"
    pairs = [f"models/{npu}:NPU"] if npu else []
    if (models / cpu).exists():
        pairs.append(f"models/{cpu}:CPU")
    return pairs


def _bench_run(body: dict[str, object]) -> dict[str, object]:
    """Start a short benchmark in the background (laptop only). Results land in /api/bench."""
    import subprocess

    kind = "detector" if body.get("kind") == "detector" else "pattern"
    seconds = int(min(max(float(body.get("seconds", 10)), 3), 60))  # type: ignore[arg-type]
    pairs = _bench_pairs(kind)
    if not pairs:
        return {"error": f"no {kind} model files found in models/"}
    cmd = [sys.executable, str(HERE / "bench" / "benchmark.py"), "--kind", kind, "--seconds", str(seconds), "--rate", "max"]
    for p in pairs:
        cmd += ["--pte", p]
    with BENCH_LOCK:
        if BENCH_JOB["running"]:
            return {"error": "a benchmark is already running"}
        BENCH_JOB.update(running=True, kind=kind, started=time.strftime("%H:%M:%S"), log="", error=None, cmd=" ".join(cmd[1:]))
    ENGINE.log("bench", f"Benchmark started: {kind}, {seconds} s per backend ({', '.join(p.split(':')[1] for p in pairs)})")

    def work() -> None:
        try:
            done = subprocess.run(cmd, cwd=HERE.parent, capture_output=True, text=True, timeout=seconds * 4 + 240)
            BENCH_JOB["log"] = (done.stdout or "")[-4000:]
            if done.returncode:
                BENCH_JOB["error"] = (done.stderr or "benchmark failed")[-1500:]
        except Exception as exc:  # noqa: BLE001
            BENCH_JOB["error"] = repr(exc)
        finally:
            BENCH_JOB["running"] = False
            ENGINE.log("bench", f"Benchmark finished: {kind}" + (" (error)" if BENCH_JOB["error"] else ""))

    threading.Thread(target=work, daemon=True, name="bench").start()
    return {"ok": True, "pairs": pairs}


def _tracker(cam: str) -> Tracker:
    if cam not in TRACKERS:
        TRACKERS[cam] = Tracker()
    return TRACKERS[cam]


def _frame(body: dict[str, object]) -> dict[str, object]:
    """Analyse one camera frame in memory. It is never written to disk or sent anywhere."""
    if DETECTOR is None:
        return {"error": "no camera model loaded: start the server with --camera-model"}
    cam = str(body.get("camera", "hall12"))
    active, why = ENGINE.camera_active(cam)
    if not active:
        # Camera is off (or outside its schedule): the frame is dropped without being decoded or analysed.
        return {"paused": True, "reason": why, "tracks": [], "crossings": [], "people": 0}
    try:
        jpeg = base64.b64decode(str(body["jpeg"]).split(",")[-1], validate=True)
    except ValueError:
        raise ApiError(400, "jpeg must be base64 (optionally a data: URL)") from None
    if not jpeg:
        raise ApiError(400, "jpeg is empty")
    try:
        with DETECT_LOCK:
            boxes, ms = DETECTOR.detect(jpeg)  # type: ignore[attr-defined]
    except (OSError, ValueError, SyntaxError) as exc:   # PIL can't read it
        raise ApiError(400, f"could not read this image ({type(exc).__name__})") from None
    finally:
        del jpeg
    roi = body.get("roi")
    if roi:
        # The page sent a zoomed-in crop around the doorway: map boxes back to full-frame coordinates,
        # which is where the doorway line lives.
        x0, y0, size = (float(v) for v in roi)  # type: ignore[union-attr]
        for b in boxes:
            b["x1"], b["x2"] = x0 + b["x1"] * size, x0 + b["x2"] * size
            b["y1"], b["y2"] = y0 + b["y1"] * size, y0 + b["y2"] * size
    with TRACK_LOCK:
        tracker = _tracker(cam)
        crossings = tracker.update(boxes)
        tracks, people, has_door = tracker.view(), tracker.people, tracker.doorway is not None
    ENGINE.camera(cam, people, ms, crossings, has_door)
    with ENGINE.lock:
        if cam in ENGINE.cameras:
            # Positions only (no pixels), so the dashboard can draw a privacy-safe "radar" of the doorway.
            ENGINE.cameras[cam]["tracks"] = [{k: t[k] for k in ("id", "x1", "y1", "x2", "y2", "side", "confirmed")}
                                             | {"trail": t["trail"][-8:]} for t in tracks]
    return {"tracks": tracks, "crossings": crossings, "people": people, "ms": round(ms, 1),
            "model": DETECTOR.name, "device": DETECTOR.device}  # type: ignore[attr-defined]


def _camera_config(body: dict[str, object]) -> dict[str, object]:
    """Set the doorway line (line=null clears it), and/or turn analysis on/off or night-only."""
    cam = str(body.get("camera", "hall12"))
    if "enabled" in body or "schedule" in body:
        out = ENGINE.set_camera(cam, bool(body["enabled"]) if "enabled" in body else None,
                                str(body["schedule"]) if "schedule" in body else None, str(body.get("by", "staff")))
        if out.get("error") or "line" not in body:
            with TRACK_LOCK:
                if not ENGINE.camera_active(cam)[0] and cam in TRACKERS:
                    TRACKERS[cam].tracks = []
            return out
    line = body.get("line")
    inside = int(body.get("inside", 1))  # type: ignore[arg-type]
    with TRACK_LOCK:
        _tracker(cam).set_doorway(list(line) if line else None, inside)  # type: ignore[arg-type]
    with ENGINE.lock:
        if cam in ENGINE.cameras:
            ENGINE.cameras[cam]["doorway"] = {"line": line, "inside": inside} if line else None
    return {"ok": True}


def _restore_state(path: Path, fresh: bool) -> None:
    """Restore tonight's saved alerts/cameras/clock after a restart, and redraw saved doorway lines."""
    STATE_INFO.update(file=path.name, restored=False)
    if fresh or not path.exists():
        STATE_INFO["why"] = "--fresh" if fresh else "no saved state"
        print("State: clean night" + (" (--fresh)" if fresh else ""))
        return
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        STATE_INFO["why"] = f"unreadable ({type(exc).__name__})"
        print(f"State: {path.name} unreadable ({exc!r}), starting a clean night")
        LOG.warning("state unreadable", extra={"fields": {"file": str(path), "error": repr(exc)}})
        return
    ok, why = ENGINE.restore(data)
    STATE_INFO.update(restored=ok, why=why)
    print(f"State: {'restored' if ok else 'not restored'} from {path.name} ({why})")
    LOG.info("state restore", extra={"fields": {"restored": ok, "why": why}})
    if not ok:
        return
    with TRACK_LOCK:
        for cid, cam in ENGINE.snapshot()["cameras"].items():  # type: ignore[union-attr]
            if cam.get("doorway"):
                _tracker(cid).set_doorway(list(cam["doorway"]["line"]), int(cam["doorway"].get("inside", 1)))


class JsonLines(logging.Formatter):
    """One JSON object per line: easy to grep, and to read back after a night."""

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}",
               "level": record.levelname, "event": record.getMessage(), "thread": record.threadName}
        out.update(getattr(record, "fields", {}))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def _setup_logging(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    except OSError as exc:
        print(f"[log] can't write {path}: {exc!r} (continuing without a log file)")
        return
    handler.setFormatter(JsonLines())
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def main() -> None:
    global ENGINE, DETECTOR, PAIRING
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", type=Path, help=".pte normal-pattern model (omit = rule fallback)")
    p.add_argument("--device", default="CPU", help="label for the dashboard: NPU or CPU")
    p.add_argument("--facility", type=Path, default=HERE / "demo" / "facility.json")
    p.add_argument("--l1-timeout", type=float, default=45, help="seconds a level-1 alert waits before level 2")
    p.add_argument("--ack-timeout", type=float, default=25, help="seconds a level-2/3 alert waits before escalating")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--lan", action="store_true", help="listen on the local network so staff phones can connect")
    p.add_argument("--camera-model", type=Path, help="person detector .pte (models/person_ssdlite_*.pte)")
    p.add_argument("--camera-device", default="CPU", help="label for the dashboard: NPU or CPU")
    p.add_argument("--pin", help="fixed 6-digit pairing PIN for staff phones (default: random each start)")
    p.add_argument("--unpair-all", action="store_true", help="forget every paired phone")
    p.add_argument("--fresh", action="store_true", help="start a clean night (don't restore logs/night_state.json)")
    p.add_argument("--state", type=Path, default=STATE_FILE, help="where tonight's state is saved")
    p.add_argument("--log", type=Path, default=LOG_FILE, help="structured log file (JSON lines)")
    args = p.parse_args()
    _setup_logging(args.log)

    facility = args.facility
    if facility == HERE / "demo" / "facility.json" and SETTINGS_FILE.exists():
        facility = SETTINGS_FILE   # settings edited on this laptop win over the demo file
    ENGINE = Engine(facility, build(args.model, args.device), args.l1_timeout, args.ack_timeout)
    ENGINE.notifier = Notifier(HERE.parent / "data" / "contacts.local.json")
    ENGINE.on_log = lambda kind, text: LOG.info("engine", extra={"fields": {"kind": kind, "text": text}})
    LAN.update(on=args.lan, port=args.port)
    PAIRING = Pairing(HERE.parent / "logs" / "paired_devices.json", args.pin)
    if args.unpair_all:
        PAIRING.unpair_all()
    _restore_state(args.state, args.fresh)
    ENGINE.start_autosave(args.state)

    def loop() -> None:
        while True:
            try:
                ENGINE.tick()
            except Exception as exc:  # noqa: BLE001 - the watch loop must never stop
                print(f"[tick] error: {exc!r}")
                LOG.error("tick failed", exc_info=exc)
            time.sleep(1)

    if args.camera_model:
        from vision.detector import PersonDetector

        # Low threshold here on purpose: the tracker needs 0.5 to start following someone but
        # keeps following down to 0.3, so a half-hidden person isn't dropped.
        DETECTOR = PersonDetector(args.camera_model, args.camera_model.parent / "person_ssdlite_anchors.pt",
                                  args.camera_device, threshold=0.3)
        print(f"Camera model: {DETECTOR.name} on {DETECTOR.device}")
    threading.Thread(target=loop, daemon=True, name="tick").start()
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    print(f"Scorer: {ENGINE.scorer.name} on {ENGINE.scorer.device}")  # type: ignore[attr-defined]
    print(f"Contacts: {len(ENGINE.notifier.contacts)} (dry run until turned on at /api/contacts; Twilio "
          f"{'configured' if ENGINE.notifier.configured() else 'not configured: calls/SMS are simulated'})")
    print(f"Dashboard http://localhost:{args.port}/dashboard/   Staff http://localhost:{args.port}/staff/   "
          f"Simulator http://localhost:{args.port}/sim/   Camera http://localhost:{args.port}/camera/")
    if args.lan:
        print("LAN mode: staff phones on this network can open /staff/ using this computer's IP address.")
        print(f"Phone pairing PIN: {PAIRING.pin}   ({PAIRING.devices} phone(s) already paired)")
    httpd = ThreadingHTTPServer((host, args.port), Handler)
    LOG.info("start", extra={"fields": {"version": _version(), "port": args.port, "lan": args.lan,
                                        "scorer": ENGINE.scorer.name, "camera_model": DETECTOR.name if DETECTOR else None,  # type: ignore[attr-defined]
                                        "state": dict(STATE_INFO)}})

    def stop(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK"):   # SIGBREAK: Ctrl+Break on Windows
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), stop)
            except (OSError, ValueError):
                pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping: saving tonight's state...")
    finally:
        STOPPING.set()
        ENGINE.stop_autosave(final_save=True)
        LOG.info("stop", extra={"fields": {"saved_to": str(args.state), "alerts": len(ENGINE.alerts)}})
        httpd.server_close()
        print(f"Saved to {args.state}. Restart within the night to pick up where it left off.")


if __name__ == "__main__":
    main()
