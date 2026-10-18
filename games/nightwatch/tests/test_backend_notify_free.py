"""Free channels for the contact fan-out (everything mocked): ntfy push and macOS iMessage, combo channels,
channel availability in state, and the laptop-only test message."""
import io
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
import notify  # noqa: E402
import server  # noqa: E402
from engine import Engine  # noqa: E402
from notify import IMESSAGE_SCRIPT, IMessage, NotConfigured, Notifier, Ntfy, SendFailed, validate_contacts  # noqa: E402
from pairing import Pairing  # noqa: E402
from scorer import RuleScorer  # noqa: E402

FACILITY = ROOT / "nightwatch" / "demo" / "facility.json"
TOPIC = "nightwatch-0123456789ab"
OWNER = {"id": "owner", "role": "owner", "name": "Facility owner (Lucas)", "phone": "+15551234821",
         "topic": TOPIC, "channel": "push+imessage"}
FAMILY = {"id": "fam", "role": "family", "name": "Family of R-07", "imessage": "fam@icloud.com", "channel": "imessage",
          "residents": ["R-07"]}
EMS = {"id": "ems", "role": "emergency_services", "name": "Emergency services", "topic": TOPIC, "channel": "push"}


class FakeNtfy:
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[tuple] = []
        self.error = error

    def push(self, topic: str, text: str, level: int, who: str) -> str:
        if self.error:
            raise self.error
        self.sent.append((topic, text, level, who))
        return "sent"


class FakeIMessage:
    def __init__(self, available: bool = True) -> None:
        self.sent: list[tuple] = []
        self._available = available

    def available(self) -> bool:
        return self._available

    def send(self, to: str, text: str) -> str:
        if not self._available:
            raise NotConfigured("iMessage needs macOS")
        self.sent.append((to, text))
        return "sent"


class NoTwilio:
    def configured(self) -> bool:
        return False


def setup(tmp_path: Path, contacts: list, live: bool = True, ntfy=None, imsg=None):  # type: ignore[no-untyped-def]
    e = Engine(FACILITY, RuleScorer(), l1_timeout=0, ack_timeout=0)
    ntfy, imsg = ntfy or FakeNtfy(), imsg or FakeIMessage()
    e.notifier = Notifier(tmp_path / "contacts.local.json", NoTwilio(), ntfy, imsg)
    e.notifier.update(contacts, live)
    e.set_time("02:10")
    return e, ntfy, imsg


def settle(e: Engine) -> None:
    deadline = time.time() + 3
    while time.time() < deadline:
        with e.lock:
            if all(n["status"] not in ("queued", "calling") for a in e.alerts for n in a.notifications):
                return
        time.sleep(0.02)


def test_validation_of_topics_imessage_and_combos() -> None:
    ok = validate_contacts([OWNER, FAMILY, EMS])
    assert ok[0]["channel"] == "push+imessage" and ok[1]["imessage"] == "fam@icloud.com"
    for bad, words in [
        (dict(OWNER, topic="short"), "12-64"),
        (dict(OWNER, topic="has spaces in it!!"), "12-64"),
        (dict(OWNER, topic="", channel="push"), "ntfy topic"),
        (dict(OWNER, channel="push+push"), "channel must be"),
        (dict(OWNER, channel="fax"), "channel must be"),
        (dict(OWNER, channel="simulated+push"), "channel must be"),
        (dict(FAMILY, imessage="911"), "never dials"),
        (dict(FAMILY, imessage="not an email@"), "Apple ID"),
        (dict(FAMILY, imessage=""), "phone number or Apple ID"),
    ]:
        with pytest.raises(ValueError, match=words):
            validate_contacts([bad])
    with pytest.raises(ValueError, match="nightwatch-[0-9a-f]{12}"):          # suggests a good topic
        validate_contacts([dict(OWNER, topic="short")])


def test_live_fanout_over_push_and_imessage(tmp_path: Path) -> None:
    e, ntfy, imsg = setup(tmp_path, [OWNER, FAMILY, EMS])
    e.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})
    settle(e)
    assert [(t, lvl, who) for t, _, lvl, who in ntfy.sent] == [(TOPIC, 3, "Resident R-07")]   # EMS push stayed simulated
    assert sorted(to for to, _ in imsg.sent) == ["+15551234821", "fam@icloud.com"]          # imessage falls back to phone
    assert all(text.startswith("This is a NightWatch demo.") for _, text in imsg.sent)
    history = [h for _, h in e.alerts[0].history]
    assert "Push to Facility owner (Lucas) sent" in history and "iMessage to Family of R-07 sent" in history
    assert "Emergency services: would be called per facility policy (simulated)" in history
    assert e.view()["channels"] == {"call": False, "sms": False, "push": True, "imessage": True}


def test_dry_run_and_unavailable_imessage_stay_simulated(tmp_path: Path) -> None:
    e, ntfy, imsg = setup(tmp_path, [OWNER], live=False)
    e.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})
    assert ntfy.sent == [] and imsg.sent == []
    e, ntfy, imsg = setup(tmp_path, [OWNER], imsg=FakeIMessage(available=False))
    e.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})
    settle(e)
    n = {x["channel"]: x for x in e.alerts[0].notifications}
    assert n["imessage"]["status"] == "simulated" and n["imessage"]["detail"] == "iMessage needs macOS"
    assert n["push"]["status"] == "sent" and e.view()["channels"]["imessage"] is False


def test_push_offline_is_failed_with_reason(tmp_path: Path) -> None:
    e, ntfy, _ = setup(tmp_path, [dict(OWNER, channel="push")], ntfy=FakeNtfy(SendFailed("no internet: staff phones still alerted")))
    e.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})
    settle(e)
    n = e.alerts[0].notifications[0]
    assert n["status"] == "failed" and "no internet" in n["detail"]
    assert "Push to Facility owner (Lucas) failed: no internet: staff phones still alerted" in [h for _, h in e.alerts[0].history]


class Resp(io.BytesIO):
    status = 200

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *a: object) -> None:
        pass


def test_ntfy_adapter_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(req, timeout):  # type: ignore[no-untyped-def]
        seen.update(url=req.full_url, headers=dict(req.headers), body=req.data.decode(), timeout=timeout)
        return Resp(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.delenv("NTFY_SERVER", raising=False)
    assert Ntfy().push(TOPIC, "This is a NightWatch demo. Résident outside.", 3, "Résident R-07") == "sent"
    assert seen["url"] == f"https://ntfy.sh/{TOPIC}" and seen["timeout"] == 8
    h = seen["headers"]
    assert h["Title"] == "NightWatch: EMERGENCY R?sident R-07" and h["Priority"] == "urgent" and h["Tags"] == "rotating_light"
    assert seen["body"] == "This is a NightWatch demo. Résident outside."
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.org/")
    Ntfy().push(TOPIC, "x", 2, "R-07")
    assert seen["url"] == f"https://ntfy.example.org/{TOPIC}"
    assert seen["headers"]["Priority"] == "high" and seen["headers"]["Tags"] == "warning"

    def offline(req, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    with pytest.raises(SendFailed, match="no internet"):
        Ntfy().push(TOPIC, "x", 3, "")


def test_imessage_adapter_passes_text_as_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []
    monkeypatch.setattr(IMessage, "available", staticmethod(lambda: True))

    def run(args, **kw):  # type: ignore[no-untyped-def]
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(notify.subprocess, "run", run)
    evil = 'This is a NightWatch demo." & do shell script "rm -rf ~'
    assert IMessage().send("fam@icloud.com", evil) == "sent"
    args, kw = calls[0]
    assert args == ["osascript", "-e", IMESSAGE_SCRIPT, evil, "fam@icloud.com"] and kw["timeout"] == 10
    assert "on run argv" in IMESSAGE_SCRIPT and "NightWatch" not in IMESSAGE_SCRIPT   # no user text in the source

    def denied(args, **kw):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(args, 1, "", "execution error: Not authorized to send Apple events to Messages. (-1743)")

    monkeypatch.setattr(notify.subprocess, "run", denied)
    with pytest.raises(SendFailed, match="Automation"):
        IMessage().send("fam@icloud.com", "x")
    monkeypatch.setattr(IMessage, "available", staticmethod(lambda: False))
    with pytest.raises(NotConfigured, match="macOS"):
        IMessage().send("fam@icloud.com", "x")


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    e, ntfy, imsg = setup(tmp_path, [OWNER, EMS], live=False)
    monkeypatch.setattr(server, "ENGINE", e, raising=False)
    monkeypatch.setattr(server, "PAIRING", Pairing(tmp_path / "devices.json", pin="246810"), raising=False)
    remote = {"on": False}
    monkeypatch.setattr(Pairing, "is_local", staticmethod(lambda ip: not remote["on"]))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", remote, e, ntfy, imsg
    httpd.shutdown()


def call(url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json", **({"X-NightWatch-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_test_message_route(api) -> None:  # type: ignore[no-untyped-def]
    url, remote, e, ntfy, imsg = api
    status, out = call(url + "/api/contacts/test", {"contact_id": "owner"})
    assert status == 200 and out["live"] is False
    assert {r["status"] for r in out["results"]} == {"dry_run"} and ntfy.sent == [] and imsg.sent == []
    assert out["results"][0]["text"] == "This is a NightWatch demo test message."
    assert call(url + "/api/contacts/test", {"contact_id": "owner"})[0] == 409          # once per 10 s
    assert call(url + "/api/contacts/test", {"contact_id": "nobody"})[0] == 404
    status, out = call(url + "/api/contacts/test", {"contact_id": "ems"})
    assert out["results"][0]["status"] == "simulated" and ntfy.sent == []
    call(url + "/api/contacts", {"live": True})
    e.notifier._tested.clear()
    status, out = call(url + "/api/contacts/test", {"contact_id": "owner"})
    assert status == 200 and {r["status"] for r in out["results"]} == {"sent"}
    assert ntfy.sent[0][1] == "This is a NightWatch demo test message." and imsg.sent == [("+15551234821", ntfy.sent[0][1])]
    status, state = call(url + "/api/state")
    assert state["channels"]["push"] is True
    remote["on"] = True
    _, pair = call(url + "/api/pair", {"pin": "246810", "name": "phone"})
    assert call(url + "/api/contacts/test", {"contact_id": "owner"}, token=pair["token"])[0] == 403
    _, view = call(url + "/api/contacts", token=pair["token"])
    assert view["contacts"][0]["topic"] == "nigh••••89ab" and view["contacts"][0]["phone"] == "+1 •••• 4821"
