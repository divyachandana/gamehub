"""Local struggle-detector server. Standard library only, so it runs on Windows on ARM as-is.

    python server/app.py                                   # rule fallback, no model
    python server/app.py --model models/frustration_qnn.pte --device NPU
    python server/app.py --model models/frustration_xnnpack.pte --device CPU

Open http://localhost:8000/portal/ (the patient portal) and http://localhost:8000/dashboard/.

Endpoints (all JSON):
    POST /api/session/start   {"assist": bool}                     -> {"session": id}
    POST /api/features        {"session", "features": {...}, "context": {"field", "error", "step"}}
                                                                    -> {"score", "help": {...}|null}
    POST /api/label           {"session", "frustrated": true}      (user pressed F8)
    POST /api/session/end     {"session", "completed": bool}
    POST /api/hr              {"bpm": number, "source": str}        (from hr_bridge.py)
    GET  /api/state           everything the dashboard shows

Nothing is sent anywhere else: every request, score and log stays on this machine.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from collections import deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from help import pick_help
from scorers import build_scorer

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs" / "sessions"

TRIGGER_SCORE = 0.7      # score that counts as "struggling"
TRIGGER_SECONDS = 3      # consecutive 1 s reports above it before we step in
COOLDOWN_S = 15.0        # don't nag: at most one help message per 15 s
HR_BASELINE_S = 60.0     # first minute of heart rate = the person's resting baseline
HR_WEIGHT = 0.15         # how much heart rate can raise the behavioral score


class HeartRate:
    """Latest watch reading plus a resting baseline from the first minute."""

    def __init__(self) -> None:
        self.bpm: float | None = None
        self.source = "none"
        self.updated = 0.0
        self._first = 0.0
        self._baseline_samples: list[float] = []
        self.baseline: float | None = None

    def update(self, bpm: float, source: str) -> None:
        now = time.time()
        if self._first == 0.0:
            self._first = now
        if now - self._first <= HR_BASELINE_S:
            self._baseline_samples.append(bpm)
            self.baseline = sum(self._baseline_samples) / len(self._baseline_samples)
        self.bpm, self.source, self.updated = bpm, source, now

    @property
    def fresh(self) -> bool:
        return self.bpm is not None and time.time() - self.updated < 10

    def term(self) -> float:
        """0..1: how far above resting the heart rate is (+30 bpm = 1)."""
        if not self.fresh or self.baseline is None or self.bpm is None:
            return 0.0
        return max(0.0, min((self.bpm - self.baseline) / 30.0, 1.0))


class Session:
    """One person's attempt at the portal task."""

    def __init__(self, assist: bool) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.assist = assist
        self.started = time.time()
        self.ended: float | None = None
        self.completed: bool | None = None
        self.above = 0
        self.last_help = 0.0
        self.helps = 0
        self.labels = 0
        self.log = (LOG_DIR / f"{self.id}.jsonl").open("a", encoding="utf-8")

    def write(self, record: dict[str, object]) -> None:
        self.log.write(json.dumps({"t": round(time.time() - self.started, 2), **record}) + "\n")
        self.log.flush()


class State:
    """Everything shared between request threads."""

    def __init__(self, scorer: object) -> None:
        self.scorer = scorer
        self.hr = HeartRate()
        self.sessions: dict[str, Session] = {}
        self.current: str | None = None
        self.history: deque[dict[str, float]] = deque(maxlen=180)
        self.events: deque[dict[str, object]] = deque(maxlen=40)
        self.latencies: deque[float] = deque(maxlen=200)
        self.lock = threading.Lock()

    def event(self, kind: str, text: str) -> None:
        self.events.appendleft({"t": time.strftime("%H:%M:%S"), "kind": kind, "text": text})


STATE: State


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)  # type: ignore[arg-type]

    def log_message(self, fmt: str, *args: object) -> None:  # quiet: 1 request/s per tab
        if not self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/state":
            return self._json(_state_view())
        if self.path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/portal/")
            self.end_headers()
            return
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        routes = {
            "/api/session/start": _start,
            "/api/features": _features,
            "/api/label": _label,
            "/api/session/end": _end,
            "/api/hr": _hr,
        }
        route = routes.get(self.path)
        if route is None:
            return self._json({"error": "not found"}, 404)
        with STATE.lock:
            result = route(body)
        return self._json(result)

    def _json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def _start(body: dict[str, object]) -> dict[str, object]:
    session = Session(assist=bool(body.get("assist", True)))
    STATE.sessions[session.id] = session
    STATE.current = session.id
    STATE.history.clear()
    session.write({"type": "start", "assist": session.assist, "scorer": STATE.scorer.name})
    STATE.event("session", f"New session {session.id} (help {'ON' if session.assist else 'OFF'})")
    return {"session": session.id}


def _features(body: dict[str, object]) -> dict[str, object]:
    session = STATE.sessions.get(str(body.get("session")))
    if session is None:
        return {"error": "unknown session"}
    raw = body.get("features") or {}
    context = body.get("context") or {}
    assert isinstance(raw, dict) and isinstance(context, dict)

    behavior, latency_ms = STATE.scorer.score(raw)  # type: ignore[attr-defined]
    hr_term = STATE.hr.term()
    score = min(1.0, behavior + HR_WEIGHT * hr_term)
    STATE.latencies.append(latency_ms)
    STATE.history.append({"t": round(time.time() - session.started, 1), "score": round(score, 3),
                          "behavior": round(behavior, 3), "hr": STATE.hr.bpm or 0})
    session.write({"type": "features", "features": raw, "context": context,
                   "behavior": behavior, "hr_term": hr_term, "score": score, "latency_ms": latency_ms})

    session.above = session.above + 1 if score >= TRIGGER_SCORE else 0
    help_out = None
    now = time.time()
    if session.above >= TRIGGER_SECONDS and now - session.last_help >= COOLDOWN_S:
        message, target = pick_help(context.get("field"), context.get("error"))  # type: ignore[arg-type]
        session.last_help, session.above = now, 0
        session.write({"type": "help", "message": message, "target": target, "shown": session.assist})
        if session.assist:
            session.helps += 1
            help_out = {"message": message, "target": target}
            STATE.event("help", message)
        else:
            STATE.event("help-off", f"(help OFF) would have said: {message}")
    return {"score": round(score, 3), "help": help_out}


def _label(body: dict[str, object]) -> dict[str, object]:
    session = STATE.sessions.get(str(body.get("session")))
    if session is None:
        return {"error": "unknown session"}
    session.labels += 1
    session.write({"type": "label", "frustrated": True})
    STATE.event("label", "User pressed F8: 'I'm frustrated'")
    return {"ok": True}


def _end(body: dict[str, object]) -> dict[str, object]:
    session = STATE.sessions.get(str(body.get("session")))
    if session is None:
        return {"error": "unknown session"}
    session.ended = time.time()
    session.completed = bool(body.get("completed"))
    duration = round(session.ended - session.started, 1)
    session.write({"type": "end", "completed": session.completed, "duration_s": duration})
    STATE.event("end", f"Session {session.id}: {'completed' if session.completed else 'gave up'} in {duration} s")
    return {"ok": True, "duration_s": duration}


def _hr(body: dict[str, object]) -> dict[str, object]:
    bpm = float(body.get("bpm", 0))
    if 30 <= bpm <= 220:
        STATE.hr.update(bpm, str(body.get("source", "watch")))
    return {"ok": True}


def _state_view() -> dict[str, object]:
    with STATE.lock:
        lat = sorted(STATE.latencies)
        sessions = [
            {"id": s.id, "assist": s.assist, "completed": s.completed, "helps": s.helps, "labels": s.labels,
             "duration_s": round((s.ended or time.time()) - s.started, 1)}
            for s in STATE.sessions.values()
        ]
        return {
            "scorer": {"name": STATE.scorer.name, "device": STATE.scorer.device,  # type: ignore[attr-defined]
                       "p50_ms": round(lat[len(lat) // 2], 3) if lat else None,
                       "p95_ms": round(lat[int(len(lat) * 0.95)], 3) if lat else None},
            "hr": {"bpm": STATE.hr.bpm, "baseline": STATE.hr.baseline, "fresh": STATE.hr.fresh,
                   "source": STATE.hr.source},
            "current": STATE.current,
            "history": list(STATE.history),
            "events": list(STATE.events),
            "sessions": sessions[-12:],
            "trigger": {"score": TRIGGER_SCORE, "seconds": TRIGGER_SECONDS},
        }


def main() -> None:
    global STATE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, help=".pte frustration model (omit = rule fallback)")
    parser.add_argument("--device", default="CPU", help="label shown on the dashboard: NPU or CPU")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE = State(build_scorer(args.model, args.device))
    print(f"Scorer: {STATE.scorer.name} on {STATE.scorer.device}")  # type: ignore[attr-defined]
    print(f"Portal:    http://localhost:{args.port}/portal/")
    print(f"Dashboard: http://localhost:{args.port}/dashboard/")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
