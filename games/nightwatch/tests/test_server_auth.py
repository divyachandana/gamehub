"""End to end over HTTP: an unpaired 'phone' is refused, a paired one works, only the laptop sees the PIN."""
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


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    server.ENGINE = Engine(ROOT / "nightwatch" / "demo" / "facility.json", RuleScorer(), 20, 15)
    server.PAIRING = Pairing(tmp_path / "devices.json", pin="246810")
    remote = {"on": True}
    monkeypatch.setattr(Pairing, "is_local", staticmethod(lambda ip: not remote["on"]))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", remote
    httpd.shutdown()


def call(url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json", **({"X-NightWatch-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_phone_must_pair_before_seeing_or_answering_alerts(base) -> None:  # type: ignore[no-untyped-def]
    url, remote = base
    assert call(url + "/api/state")[0] == 401
    assert call(url + "/api/ack", {"alert": 1})[0] == 401
    with urllib.request.urlopen(url + "/staff/", timeout=5) as r:
        assert r.status == 200                                   # pages themselves hold no data
    assert call(url + "/api/pair", {"pin": "111111"}) == (403, {"error": "wrong PIN"})
    status, out = call(url + "/api/pair", {"pin": "246810", "name": "test phone"})
    assert status == 200
    status, state = call(url + "/api/state", token=out["token"])
    assert status == 200 and "pairing" not in state              # phones never see the PIN
    remote["on"] = False                                         # now act as the laptop itself
    status, state = call(url + "/api/state")
    assert status == 200 and state["pairing"] == {"pin": "246810", "devices": 1}


def test_settings_can_only_be_changed_on_the_laptop(base) -> None:  # type: ignore[no-untyped-def]
    url, remote = base
    _, out = call(url + "/api/pair", {"pin": "246810", "name": "phone"})
    status, body = call(url + "/api/settings", {"facility": "x"}, token=out["token"])
    assert status == 403 and "laptop" in body["error"]
    status, health = call(url + "/api/health", token=out["token"])
    assert status == 200 and "phones" not in health          # phones never get the PIN/address
