"""Contact fan-out (calls/SMS via Twilio) with the network mocked: dry run by default, emergency services
always simulated, emergency short codes refused, rate limits, alert-facts-only messages."""
import io
import json
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
from engine import SCENARIOS, Engine  # noqa: E402
from notify import Notifier, Offline, SendFailed, Twilio, clean_phone, mask, validate_contacts  # noqa: E402
from pairing import Pairing  # noqa: E402
from scorer import RuleScorer  # noqa: E402

FACILITY = ROOT / "nightwatch" / "demo" / "facility.json"
OWNER = {"id": "owner", "role": "owner", "name": "Facility owner (Lucas)", "phone": "+15551234821", "channel": "call+sms"}
ONCALL = {"id": "oncall", "role": "on_call", "name": "On-call supervisor", "phone": "+15559870001", "channel": "call"}
FAMILY = {"id": "fam", "role": "family", "name": "Family of R-07", "phone": "+15559870002", "channel": "sms", "residents": ["R-07"]}
EMS = {"id": "ems", "role": "emergency_services", "name": "Emergency services", "phone": "", "channel": "call"}


class FakeTwilio:
    def __init__(self, configured: bool = True, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self._configured, self.error = configured, error

    def configured(self) -> bool:
        return self._configured

    def call(self, to: str, text: str) -> str:
        return self._do("call", to, text)

    def sms(self, to: str, text: str) -> str:
        return self._do("sms", to, text)

    def _do(self, ch: str, to: str, text: str) -> str:
        if self.error:
            raise self.error
        self.sent.append((ch, to, text))
        return "queued"


def setup(tmp_path: Path, contacts: list, live: bool = True, transport: FakeTwilio | None = None) -> tuple[Engine, FakeTwilio]:
    e = Engine(FACILITY, RuleScorer(), l1_timeout=0, ack_timeout=0)
    t = transport or FakeTwilio()
    e.notifier = Notifier(tmp_path / "contacts.local.json", t)
    e.notifier.update(contacts, live)
    e.set_time("02:10")
    return e, t


def settle(e: Engine, timeout: float = 3) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with e.lock:
            if all(n["status"] not in ("queued", "calling") or n.get("detail") for a in e.alerts for n in a.notifications):
                return
        time.sleep(0.02)


def outside(e: Engine) -> None:
    e.residents["R-07"].hr = 120                                              # must never reach a contact
    e.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})


@pytest.mark.parametrize("bad", ["911", "112", "999", "000", "110", "119", "118", "08", "+911", "1234567", "+1 555 12"])
def test_emergency_and_short_numbers_are_refused_for_every_role(bad: str) -> None:
    with pytest.raises(ValueError):
        clean_phone(bad)
    for role in notify.ROLES:
        with pytest.raises(ValueError, match="never dials|international"):
            validate_contacts([{"id": "x", "role": role, "name": "X", "phone": bad, "channel": "call"}])


def test_phone_cleaning_and_masking() -> None:
    assert clean_phone("+1 (555) 123-4821") == "+15551234821"
    assert mask("+15551234821") == "+1 •••• 4821"
    with pytest.raises(ValueError, match="international"):
        clean_phone("5551234821")
    with pytest.raises(ValueError, match="phone number is needed"):
        validate_contacts([{"id": "x", "role": "owner", "name": "X", "phone": "", "channel": "sms"}])


def test_default_is_dry_run_with_seeded_simulated_contacts(tmp_path: Path) -> None:
    n = Notifier(tmp_path / "contacts.local.json", FakeTwilio())
    assert not n.live and [c["role"] for c in n.contacts] == ["on_call", "family", "owner", "emergency_services"]
    assert json.loads((tmp_path / "contacts.local.json").read_text())["contacts"][0]["channel"] == "simulated"
    e, t = setup(tmp_path, [OWNER, ONCALL, EMS], live=False)
    outside(e)
    a = e.alerts[0]
    assert t.sent == [] and {x["status"] for x in a.notifications} == {"simulated"}
    assert any("dry run" in h for _, h in a.history)


def test_live_fanout_calls_texts_and_logs_each_contact(tmp_path: Path) -> None:
    e, t = setup(tmp_path, [OWNER, ONCALL, FAMILY, EMS])
    outside(e)
    settle(e)
    a = e.alerts[0]
    assert sorted((ch, to) for ch, to, _ in t.sent) == [("call", "+15551234821"), ("call", "+15559870001"),
                                                        ("sms", "+15551234821"), ("sms", "+15559870002")]
    history = [h for _, h in a.history]
    assert "Calling Facility owner (Lucas) … ringing" in history
    assert "SMS to Family of R-07 sent" in history
    assert "Emergency services: would be called per facility policy (simulated)" in history
    ems = [x for x in a.notifications if x["role"] == "emergency_services"]
    assert len(ems) == 1 and ems[0]["status"] == "simulated"
    view = e.view()
    assert view["contacts_live"] is True and view["twilio_configured"] is True
    assert {x["status"] for x in view["alerts"][0]["notifications"]} == {"calling", "sent", "simulated"}
    text = t.sent[0][2]
    assert text.startswith("This is a NightWatch demo.") and "outside" in text and "-3 degrees" in text
    assert "120" not in text and "heart" not in text.lower()
    assert all("+1555" not in ev["text"] for ev in e.events)                  # numbers never in the log


def test_emergency_services_are_never_dialled_even_live_with_a_number(tmp_path: Path) -> None:
    e, t = setup(tmp_path, [dict(EMS, phone="+15550000999", channel="call+sms")])
    outside(e)
    settle(e)
    assert t.sent == []


def test_family_only_for_their_resident(tmp_path: Path) -> None:
    e, t = setup(tmp_path, [FAMILY])
    e.sensor({"type": "location", "resident": "R-21", "zone": "outside", "exit": "garden"})
    e.sensor({"type": "door", "exit": "service", "open": True})
    e.tick()
    settle(e)
    assert t.sent == []
    outside(e)
    settle(e)
    assert [to for _, to, _ in t.sent] == ["+15559870002"]


def test_at_most_five_real_per_fanout_and_hourly_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    many = [dict(ONCALL, id=f"c{i}", phone=f"+1555987{i:04d}") for i in range(7)]
    e, t = setup(tmp_path, many)
    outside(e)
    settle(e)
    assert len(t.sent) == 5
    assert sum(1 for x in e.alerts[0].notifications if "limit of 5" in x["detail"]) == 2
    monkeypatch.setattr(notify, "MAX_REAL_PER_HOUR", 6)
    e.resolve(1, "Found safe")
    e.sensor({"type": "location", "resident": "R-21", "zone": "outside", "exit": "garden"})
    settle(e)
    assert len(t.sent) == 6 and any("hourly limit" in x["detail"] for x in e.alerts[1].notifications)


def test_manual_call_for_help_once_per_60_s(tmp_path: Path) -> None:
    e, t = setup(tmp_path, [ONCALL])
    e.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    out = e.notify(1, "Night nurse")
    assert out["ok"] and out["real"] == 1
    again = e.notify(1, "Night nurse")
    assert "already notified" in again["error"]
    e.alerts[0].last_fanout -= 61
    assert e.notify(1, "Night nurse")["ok"]
    settle(e)
    assert len(t.sent) == 2
    assert "error" in e.notify(99, "x")


def test_auto_fanout_only_once_when_level_3_is_first_reached(tmp_path: Path) -> None:
    e, t = setup(tmp_path, [ONCALL])
    e.sensor({"type": "door", "exit": "front", "open": True})                 # level 2
    for _ in range(4):
        e.tick()                                                              # -> 3, then more contact steps
    settle(e)
    assert e.alerts[0].level == 3 and len(t.sent) == 1


def test_not_configured_offline_and_twilio_errors_are_reported(tmp_path: Path) -> None:
    e, t = setup(tmp_path, [ONCALL], transport=FakeTwilio(configured=False))
    outside(e)
    assert e.alerts[0].notifications[0]["detail"] == "Twilio not configured" and t.sent == []
    e, t = setup(tmp_path, [ONCALL], transport=FakeTwilio(error=Offline("no internet: staff phones still alerted")))
    outside(e)
    settle(e)
    n = e.alerts[0].notifications[0]
    assert n["status"] == "simulated" and "no internet" in n["detail"]
    e, t = setup(tmp_path, [ONCALL], transport=FakeTwilio(error=SendFailed("Twilio said HTTP 400: bad")))
    outside(e)
    settle(e)
    assert e.alerts[0].notifications[0]["status"] == "failed"
    assert any("Call to On-call supervisor failed" in h for _, h in e.alerts[0].history)


def test_simulated_camera_alerts_never_call_anyone(tmp_path: Path) -> None:
    e, t = setup(tmp_path, [ONCALL])
    for _, action in SCENARIOS["camera_walkout"][1:]:
        action(e)
    e.notify(e.alerts[0].id, "presenter")
    settle(e)
    assert t.sent == [] and "simulated camera" in e.alerts[0].notifications[0]["detail"]


def test_twilio_adapter_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"):
        monkeypatch.delenv(k, raising=False)
    assert not Twilio.configured()
    with pytest.raises(notify.NotConfigured):
        Twilio().call("+15551234821", "hi")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token")
    monkeypatch.setenv("TWILIO_FROM", "+15550001111")
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *a: object) -> None:
            pass

    def fake_urlopen(req, timeout):  # type: ignore[no-untyped-def]
        seen.update(url=req.full_url, auth=req.headers["Authorization"], body=req.data.decode(), timeout=timeout)
        return Resp(b'{"status": "queued"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert Twilio().call("+15551234821", "This is a NightWatch demo. A & B") == "queued"
    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Calls.json" and seen["timeout"] == 8
    assert seen["auth"].startswith("Basic ") and "secret-token" not in seen["body"]
    body = urllib.parse.parse_qs(seen["body"])
    assert body["To"] == ["+15551234821"] and body["From"] == ["+15550001111"]
    assert body["Twiml"][0].count("<Say") == 2 and '<Pause length="1"/>' in body["Twiml"][0] and "A &amp; B" in body["Twiml"][0]
    Twilio().sms("+15551234821", "hello")
    assert seen["url"].endswith("/Messages.json") and "Body=hello" in seen["body"]

    def offline(req, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    with pytest.raises(Offline):
        Twilio().sms("+15551234821", "x")


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    e, t = setup(tmp_path, [OWNER, EMS], live=False)
    monkeypatch.setattr(server, "ENGINE", e, raising=False)
    monkeypatch.setattr(server, "PAIRING", Pairing(tmp_path / "devices.json", pin="246810"), raising=False)
    remote = {"on": False}
    monkeypatch.setattr(Pairing, "is_local", staticmethod(lambda ip: not remote["on"]))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", remote, e, t
    httpd.shutdown()


def call(url: str, body: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json", **({"X-NightWatch-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_contacts_api(api) -> None:  # type: ignore[no-untyped-def]
    url, remote, e, t = api
    status, out = call(url + "/api/contacts")
    assert status == 200 and out["contacts"][0]["phone"] == "+15551234821" and out["live"] is False
    status, out = call(url + "/api/contacts", {"contacts": [dict(OWNER, phone="911")]})
    assert status == 400 and "never dials" in out["error"]
    status, out = call(url + "/api/contacts", {"live": True})
    assert status == 200 and out["live"] is True and call(url + "/api/state")[1]["contacts_live"] is True
    assert any("turned ON" in ev["text"] for ev in e.events)
    call(url + "/api/clock", {"time": "02:10"})
    call(url + "/api/sensor", {"type": "location", "resident": "R-07", "zone": "corridor"})
    status, out = call(url + "/api/notify", {"alert": 1, "by": "Night nurse"})
    assert status == 200 and out["real"] == 2
    assert call(url + "/api/notify", {"alert": 1})[0] == 409
    assert call(url + "/api/notify", {"alert": 42})[0] == 404
    remote["on"] = True
    _, pair = call(url + "/api/pair", {"pin": "246810", "name": "phone"})
    status, out = call(url + "/api/contacts", token=pair["token"])
    assert status == 200 and out["contacts"][0]["phone"] == "+1 •••• 4821"
    assert call(url + "/api/contacts", {"live": False}, token=pair["token"])[0] == 403
    settle(e)
    assert len(t.sent) == 2
