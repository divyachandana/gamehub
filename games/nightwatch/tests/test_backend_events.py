"""GET /api/events: Server-Sent Events push a ping on every change, heartbeats when quiet, and honour pairing."""
import http.client
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
import server  # noqa: E402
from engine import Engine  # noqa: E402
from pairing import Pairing  # noqa: E402
from scorer import RuleScorer  # noqa: E402


@pytest.fixture
def srv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(server, "ENGINE", Engine(ROOT / "nightwatch" / "demo" / "facility.json", RuleScorer(), 20, 15), raising=False)
    monkeypatch.setattr(server, "PAIRING", Pairing(tmp_path / "devices.json", pin="246810"), raising=False)
    monkeypatch.setattr(server, "SSE_HEARTBEAT_S", 0.3)
    remote = {"on": False}
    monkeypatch.setattr(Pairing, "is_local", staticmethod(lambda ip: not remote["on"]))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], remote
    server.STOPPING.set()
    httpd.shutdown()
    server.STOPPING.clear()


class Stream:
    def __init__(self, port: int, path: str) -> None:
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.conn.request("GET", path)
        self.resp = self.conn.getresponse()

    def next(self, name: str, timeout: float = 4) -> dict:
        deadline = time.time() + timeout
        event, data = None, None
        while time.time() < deadline:
            line = self.resp.fp.readline().decode().rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and event:
                if event == name:
                    return data  # type: ignore[return-value]
                event, data = None, None
        raise AssertionError(f"no {name} event")

    def close(self) -> None:
        self.conn.close()


def post(port: int, path: str, body: dict) -> int:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    status = c.getresponse().status
    c.close()
    return status


def test_change_is_pushed_then_heartbeats(srv) -> None:  # type: ignore[no-untyped-def]
    port, _ = srv
    s = Stream(port, "/api/events")
    assert s.resp.status == 200 and s.resp.getheader("Content-Type").startswith("text/event-stream")
    first = s.next("state")
    assert first["open_alerts"] == 0 and "clock" in first
    assert post(port, "/api/clock", {"time": "02:10"}) == 200
    assert post(port, "/api/sensor", {"type": "door", "exit": "front", "open": True}) == 200
    deadline = time.time() + 4
    while True:
        ev = s.next("state")
        if ev["open_alerts"] == 1:
            break
        assert time.time() < deadline
    assert ev["top_level"] == 2 and ev["unacked"] == 1 and ev["rev"] > first["rev"]
    assert "rev" in s.next("heartbeat")
    s.close()


def test_full_view_and_pin_only_for_the_laptop(srv) -> None:  # type: ignore[no-untyped-def]
    port, remote = srv
    s = Stream(port, "/api/events?full=1")
    view = s.next("state")
    assert "residents" in view and view["pairing"]["pin"] == "246810"
    s.close()
    remote["on"] = True
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/events")
    assert c.getresponse().status == 401
    c.close()
    ok, token = server.PAIRING.pair("10.0.0.9", "246810", "phone")
    assert ok
    s = Stream(port, f"/api/events?full=1&token={token}")
    view = s.next("state")
    assert s.resp.status == 200 and "residents" in view and "pairing" not in view
    s.close()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", f"/api/state?token={token}")                      # the URL token only works for the stream
    assert c.getresponse().status == 401
    c.close()


def test_stream_count_is_capped(srv, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    port, _ = srv
    monkeypatch.setattr(server, "SSE_MAX_CLIENTS", 1)
    a = Stream(port, "/api/events")
    a.next("state")
    b = Stream(port, "/api/events")
    assert b.resp.status == 503
    a.close()
    b.close()
