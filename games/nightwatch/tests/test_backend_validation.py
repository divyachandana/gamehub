"""Every POST body is checked: bad input gets a clear 4xx with {"error"}, never a 500 or a dropped connection."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
import server  # noqa: E402
from engine import Engine  # noqa: E402
from pairing import Pairing  # noqa: E402
from scorer import RuleScorer  # noqa: E402


class FakeDetector:
    name, device = "fake", "CPU"

    def detect(self, jpeg: bytes) -> tuple[list[dict[str, float]], float]:
        if not jpeg.startswith(b"\xff\xd8"):
            raise OSError("cannot identify image file")
        return [], 1.0


@pytest.fixture(scope="module")
def httpd():  # type: ignore[no-untyped-def]
    h = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=h.serve_forever, daemon=True).start()
    yield h
    h.shutdown()


@pytest.fixture
def url(httpd, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(server, "ENGINE", Engine(ROOT / "nightwatch" / "demo" / "facility.json", RuleScorer(), 20, 15), raising=False)
    monkeypatch.setattr(server, "PAIRING", Pairing(tmp_path / "devices.json", pin="246810"), raising=False)
    monkeypatch.setattr(server, "SETTINGS_FILE", tmp_path / "facility.local.json")
    monkeypatch.setattr(server, "TRACKERS", {})
    monkeypatch.setattr(server, "DETECTOR", None)
    return f"http://127.0.0.1:{httpd.server_address[1]}"


def post(url: str, body: object, ctype: str = "application/json", raw: bytes | None = None) -> tuple[int, dict]:
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(url, data, {"Content-Type": ctype} if ctype else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


BAD = [
    # route, body, expected status, words in the error
    ("/api/sensor", {}, 400, "type is required"),
    ("/api/sensor", {"type": "smoke"}, 400, "type must be one of"),
    ("/api/sensor", {"type": "door"}, 400, "exit is required"),
    ("/api/sensor", {"type": "door", "exit": "moon"}, 404, "unknown exit"),
    ("/api/sensor", {"type": "door", "exit": "front", "open": "maybe"}, 400, "open must be true or false"),
    ("/api/sensor", {"type": "bed", "resident": "R-99", "in_bed": False}, 404, "unknown resident"),
    ("/api/sensor", {"type": "bed", "resident": "R-07"}, 400, "in_bed is required"),
    ("/api/sensor", {"type": "hr", "resident": "R-07", "bpm": "fast"}, 400, "bpm must be a number"),
    ("/api/sensor", {"type": "hr", "resident": "R-07", "bpm": 900}, 400, "between 20 and 250"),
    ("/api/sensor", {"type": "motion", "resident": "R-07", "level": 2}, 400, "between 0 and 1"),
    ("/api/sensor", {"type": "location", "resident": "R-07", "zone": "<script>"}, 400, "zone must be an id"),
    ("/api/sensor", {"type": "location", "resident": ["R-07"], "zone": "corridor"}, 400, "resident must be an id"),
    ("/api/ack", {}, 400, "alert is required"),
    ("/api/ack", {"alert": "one"}, 400, "whole number"),
    ("/api/ack", {"alert": 1.5}, 400, "whole number"),
    ("/api/ack", {"alert": True}, 400, "whole number"),
    ("/api/ack", {"alert": 999}, 404, "no such open alert"),
    ("/api/ack", {"alert": 1, "by": "x" * 201}, 400, "longer than 200"),
    ("/api/resolve", {"alert": 999}, 404, "no such open alert"),
    ("/api/resolve", {"alert": 1, "outcome": {"a": 1}}, 400, "outcome must be text"),
    ("/api/backup", {"alert": 999}, 404, "no such open alert"),
    ("/api/clock", {}, 400, "time is required"),
    ("/api/clock", {"time": "25:00"}, 400, "HH:MM"),
    ("/api/clock", {"time": "2am"}, 400, "HH:MM"),
    ("/api/clock", {"time": "02:00", "speed": 0}, 400, "speed must be between"),
    ("/api/clock", {"time": "02:00", "speed": "warp"}, 400, "speed must be a number"),
    ("/api/scenario", {}, 400, "name is required"),
    ("/api/scenario", {"name": "zombies"}, 404, "unknown scenario"),
    ("/api/record", {"alert": 999, "affect": "Calm", "intervention": "Reassurance"}, 404, "no such alert"),
    ("/api/record", {"alert": 1, "note": "x" * 501}, 400, "longer than 500"),
    ("/api/camera/config", {"camera": "moon", "enabled": False}, 404, "unknown camera"),
    ("/api/camera/config", {"camera": "hall12", "enabled": "nope"}, 400, "enabled must be true or false"),
    ("/api/camera/config", {"camera": "hall12", "schedule": "weekends"}, 400, "schedule must be one of"),
    ("/api/camera/config", {"camera": "hall12", "line": [0, 0, 1]}, 400, "list of 4 numbers"),
    ("/api/camera/config", {"camera": "hall12", "line": [0.5, 0.5, 0.5, 0.5]}, 400, "two different points"),
    ("/api/camera/config", {"camera": "hall12", "line": [0, 0, 1, "x"]}, 400, "must be a number"),
    ("/api/camera/config", {"camera": "hall12", "line": [0, 0, 1, 1], "inside": 0}, 400, "inside must be 1 or -1"),
    ("/api/frame", {"camera": "hall12"}, 400, "jpeg is required"),
    ("/api/frame", {"camera": "hall12", "jpeg": "AAAA", "roi": [0, 0]}, 400, "list of 3 numbers"),
    ("/api/frame", {"camera": "hall12", "jpeg": "AAAA"}, 503, "no camera model loaded"),
    ("/api/settings", {"facility": "x"}, 400, ""),
    ("/api/settings", {"facility": "Wing", "night_staff": 1, "outside_temp_c": 0, "residents": "lots"}, 400, "malformed"),
    ("/api/bench/run", {"kind": "gpu"}, 400, "kind must be one of"),
    ("/api/pair", {"pin": ["1"]}, 400, "pin must be text"),
    ("/api/nope", {}, 404, "no such API route"),
]


@pytest.mark.parametrize("route,body,status,words", BAD, ids=[f"{r}-{i}" for i, (r, *_) in enumerate(BAD)])
def test_bad_inputs_get_clear_errors(url: str, route: str, body: dict, status: int, words: str) -> None:
    got_status, out = post(url + route, body)
    assert got_status == status, out
    assert words in out["error"]


def test_body_must_be_a_json_object_with_json_content_type(url: str) -> None:
    assert post(url + "/api/ack", None, raw=b"{not json")[0] == 400
    assert post(url + "/api/ack", [1, 2])[1]["error"] == "the body must be a JSON object"
    status, out = post(url + "/api/ack", {"alert": 1}, ctype="text/plain")
    assert status == 415 and "application/json" in out["error"]
    assert post(url + "/api/ack", {"alert": 1}, ctype="")[0] == 415
    assert post(url + "/api/ack", None, raw=b"\xff\xfe")[0] == 400


def test_bodies_are_size_capped(url: str) -> None:
    status, out = post(url + "/api/sensor", {"type": "hr", "pad": "x" * 70_000})
    assert status == 413 and "limit 65536" in out["error"]
    status, out = post(url + "/api/frame", {"camera": "hall12", "jpeg": "A" * (2 * 1024 * 1024 + 10)})
    assert status == 413


def test_conflicts_are_409(url: str) -> None:
    assert post(url + "/api/clock", {"time": "02:10"})[0] == 200
    assert post(url + "/api/sensor", {"type": "door", "exit": "front", "open": True}) == (200, {"ok": True})
    status, out = post(url + "/api/record", {"alert": 1, "affect": "Calm", "intervention": "Reassurance"})
    assert status == 409 and "still open" in out["error"]
    assert post(url + "/api/resolve", {"alert": 1, "outcome": "ok"})[0] == 200
    status, out = post(url + "/api/ack", {"alert": 1})
    assert status == 409 and "already resolved" in out["error"]


def test_good_inputs_still_work_like_before(url: str) -> None:
    assert post(url + "/api/clock", {"time": "02:10", "speed": 1}) == (200, {"ok": True})
    assert post(url + "/api/sensor", {"type": "location", "resident": "R-07", "zone": "corridor"}) == (200, {"ok": True})
    assert post(url + "/api/sensor", {"type": "bed", "resident": "R-15", "in_bed": "false"}) == (200, {"ok": True})
    assert server.ENGINE.residents["R-15"].in_bed is False                    # "false" is false, not truthy
    assert post(url + "/api/ack", {"alert": "1", "by": "Nurse"}) == (200, {"ok": True})
    assert post(url + "/api/camera/config", {"camera": "hall12", "line": [0.4, 0.1, 0.5, 0.9], "inside": -1}) == (200, {"ok": True})
    assert server.ENGINE.cameras["hall12"]["doorway"] == {"line": [0.4, 0.1, 0.5, 0.9], "inside": -1}
    assert post(url + "/api/camera/config", {"camera": "hall12", "enabled": False, "by": "t"}) == (200, {"ok": True})
    status, out = post(url + "/api/scenario", {"name": "zombies"})
    assert "night_elopement" in out["choices"]
    assert get(url + "/api/resident?id=R-07")[0] == 200


def test_frames_with_a_detector(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "DETECTOR", FakeDetector())
    assert post(url + "/api/frame", {"camera": "moon", "jpeg": "AAAA"})[0] == 404
    status, out = post(url + "/api/frame", {"camera": "hall12", "jpeg": "not base64!!"})
    assert status == 400 and "base64" in out["error"]
    status, out = post(url + "/api/frame", {"camera": "hall12", "jpeg": "AAAA"})
    assert status == 400 and "could not read this image" in out["error"]
    status, out = post(url + "/api/frame", {"camera": "hall12", "jpeg": "data:image/jpeg;base64,/9j/AA=="})
    assert status == 200 and out["people"] == 0


def test_get_errors(url: str) -> None:
    assert get(url + "/api/resident?id=nobody")[0] == 404
    assert get(url + "/api/resident")[0] == 400
    assert get(url + "/api/whatever")[0] == 404
    assert get(url + "/api/state?cache=1")[0] == 200
