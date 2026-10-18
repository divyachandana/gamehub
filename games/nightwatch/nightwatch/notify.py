"""Emergency contact fan-out, demo-grade and safe by default. Channels:

    call, sms   Twilio (paid; needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM)
    push        ntfy (free): POST https://ntfy.sh/<topic>; the contact installs the ntfy app and
                subscribes to that topic. NTFY_SERVER=https://your.server to self-host.
    imessage    macOS only (free): the laptop's Messages app sends it, via osascript
    simulated   logged only
Combine with "+", e.g. "push+imessage" or "call+sms".

Hard rules (tested in tests/test_backend_notify.py):
- Dry run by default: nothing real is sent until the nurse-station laptop turns contacts_live on
  (POST /api/contacts {"live": true}). It is never saved, so every server start is a dry run again.
- Emergency services are ALWAYS simulated, whatever their channel says.
- Emergency short codes (911, 112, 999, 000, 110, 119, 118, 08 ...) and anything shorter than
  8 digits are refused as contact numbers, for every role.
- At most one real fan-out per alert per 60 s, at most 5 real calls/SMS per fan-out, and at most
  20 real calls/SMS per hour across the whole server.
- Credentials come only from the environment (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM):
  never logged, never in the state file, never in any file this server writes.
- Messages carry alert facts only (who, where, time, level, outside temperature), never video and
  never health readings, and always start with "This is a NightWatch demo."
- No internet / no Twilio: the contact is marked "simulated" with the reason; staff phones are
  alerted by the local server either way. Sending happens in background threads, so an alert
  never waits on the network (8 s timeout per request).
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path
from xml.sax.saxutils import escape

ROLES = ("on_call", "family", "owner", "emergency_services", "other")
BASE_CHANNELS = ("call", "sms", "push", "imessage")
CHANNELS = ("call", "sms", "call+sms", "push", "imessage", "push+imessage", "simulated")   # common picks; any "+" combo works
TOPIC = re.compile(r"^[A-Za-z0-9_-]{12,64}$")
EMAIL = re.compile(r"^[^@\s\"\\]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
MESSAGES_APP = Path("/System/Applications/Messages.app")
EMERGENCY_CODES = {"911", "112", "999", "000", "110", "119", "118", "08", "100", "101", "102", "999", "933"}
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
IDENT = re.compile(r"^[A-Za-z0-9_.-]{1,24}$")
FANOUT_GAP_S = 60
MAX_REAL_PER_FANOUT = 5
MAX_REAL_PER_HOUR = 20
TIMEOUT_S = 8
MAX_CONTACTS = 20
PREFIX = "This is a NightWatch demo."

DEFAULT_CONTACTS: list[dict[str, object]] = [
    {"id": "oncall", "role": "on_call", "name": "On-call supervisor", "phone": "", "channel": "simulated", "residents": []},
    {"id": "family-r07", "role": "family", "name": "Family of R-07", "phone": "", "channel": "simulated", "residents": ["R-07"]},
    {"id": "owner", "role": "owner", "name": "Facility owner", "phone": "", "channel": "simulated", "residents": []},
    {"id": "ems", "role": "emergency_services", "name": "Emergency services", "phone": "", "channel": "simulated", "residents": []},
]


class NotConfigured(Exception):
    pass


class Offline(Exception):
    pass


class SendFailed(Exception):
    pass


def clean_phone(raw: object) -> str:
    """Normalise to E.164 ('+15551234821'), or '' for none. Raises ValueError for emergency/short numbers."""
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str):
        raise ValueError("phone must be text, e.g. +15551234821")
    p = re.sub(r"[\s().-]", "", raw)
    digits = re.sub(r"\D", "", p)
    if digits in EMERGENCY_CODES or len(digits) < 8:
        raise ValueError(f"{raw!r} looks like an emergency or short number: NightWatch never dials those "
                         "(emergency services are always simulated)")
    if not E164.match(p):
        raise ValueError(f"phone {raw!r} must be in international format, e.g. +15551234821")
    return p


def mask(phone: str) -> str:
    return f"{phone[:2]} •••• {phone[-4:]}" if phone else ""


def mask_any(v: str) -> str:
    """Phones as '+1 •••• 4821', emails as 'l•••@icloud.com', topics as 'nigh••••a1b2'."""
    if not v:
        return ""
    if v.startswith("+"):
        return mask(v)
    if "@" in v:
        user, _, domain = v.partition("@")
        return f"{user[:1]}•••@{domain}"
    return f"{v[:4]}••••{v[-4:]}"


def suggest_topic() -> str:
    return "nightwatch-" + secrets.token_hex(6)


def channel_parts(channel: str) -> list[str]:
    return ["simulated"] if channel == "simulated" else channel.split("+")


def recipient(c: dict[str, object], ch: str) -> str:
    """Where a channel goes for this contact ('' = nowhere)."""
    if ch in ("call", "sms"):
        return str(c.get("phone") or "")
    if ch == "push":
        return str(c.get("topic") or "")
    if ch == "imessage":
        return str(c.get("imessage") or c.get("phone") or "")
    return ""


def validate_contacts(items: object) -> list[dict[str, object]]:
    if not isinstance(items, list):
        raise ValueError("contacts must be a list")
    if len(items) > MAX_CONTACTS:
        raise ValueError(f"at most {MAX_CONTACTS} contacts")
    out, seen = [], set()
    for i, c in enumerate(items):
        if not isinstance(c, dict):
            raise ValueError(f"contact {i + 1} must be an object")
        cid = str(c.get("id") or f"c-{secrets.token_hex(3)}")
        if not IDENT.match(cid) or cid in seen:
            raise ValueError(f"contact {i + 1}: id must be unique, 1-24 letters/digits/- _ .")
        seen.add(cid)
        role = c.get("role")
        if role not in ROLES:
            raise ValueError(f"contact {cid}: role must be one of {', '.join(ROLES)}")
        name = str(c.get("name") or "").strip()
        if not name or len(name) > 60:
            raise ValueError(f"contact {cid}: name is required (at most 60 characters)")
        channel = c.get("channel") or "simulated"
        parts = channel_parts(channel) if isinstance(channel, str) else []
        if not parts or len(set(parts)) != len(parts) or not (parts == ["simulated"] or set(parts) <= set(BASE_CHANNELS)):
            raise ValueError(f"contact {cid}: channel must be simulated, or one or more of {', '.join(BASE_CHANNELS)} "
                             "joined with + (e.g. push+imessage)")
        try:
            phone = clean_phone(c.get("phone"))
        except ValueError as exc:
            raise ValueError(f"contact {cid}: {exc}") from None
        topic = str(c.get("topic") or "").strip()
        if topic and not TOPIC.match(topic):
            raise ValueError(f"contact {cid}: ntfy topic must be 12-64 letters, digits, - or _ and hard to guess "
                             f"(anyone who knows it can read the alerts), e.g. {suggest_topic()}")
        imsg = str(c.get("imessage") or "").strip()
        if imsg:
            if "@" in imsg:
                if not EMAIL.match(imsg):
                    raise ValueError(f"contact {cid}: iMessage must be a phone (+15551234821) or an Apple ID email")
            else:
                try:
                    imsg = clean_phone(imsg)
                except ValueError as exc:
                    raise ValueError(f"contact {cid}: iMessage {exc}") from None
        rec = {"phone": phone, "topic": topic, "imessage": imsg}
        if role != "emergency_services":
            for ch in parts:
                if ch != "simulated" and not recipient(rec, ch):
                    need = {"call": "a phone number", "sms": "a phone number", "push": f"an ntfy topic (e.g. {suggest_topic()})",
                            "imessage": "a phone number or Apple ID email"}[ch]
                    raise ValueError(f"contact {cid}: {need} is needed for {ch}")
        residents = c.get("residents") or []
        if not isinstance(residents, list) or len(residents) > 50 or not all(isinstance(r, str) and IDENT.match(r) for r in residents):
            raise ValueError(f"contact {cid}: residents must be a list of resident ids")
        out.append({"id": cid, "role": role, "name": name, "phone": phone, "topic": topic, "imessage": imsg,
                    "channel": channel, "residents": residents})
    return out


class Twilio:
    """Stdlib-only Twilio REST client. Credentials are read from the environment on every call."""

    API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/{what}.json"

    @staticmethod
    def configured() -> bool:
        return all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"))

    def _post(self, what: str, fields: dict[str, str]) -> str:
        sid, token, sender = (os.environ.get(k, "") for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"))
        if not (sid and token and sender):
            raise NotConfigured("Twilio not configured")
        req = urllib.request.Request(
            self.API.format(sid=urllib.parse.quote(sid), what=what),
            urllib.parse.urlencode({**fields, "From": sender}).encode(),
            {"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode(),
             "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return str(json.loads(r.read() or b"{}").get("status") or "queued")
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read()).get("message") or exc.reason
            except (ValueError, OSError):
                msg = exc.reason
            msg = re.sub(r"\+?\d{6,}", lambda m: mask(m[0]), str(msg))[:160]   # no full numbers in history/logs
            raise SendFailed(f"Twilio said HTTP {exc.code}: {msg}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            raise Offline("no internet: staff phones still alerted") from None

    def call(self, to: str, text: str) -> str:
        say = escape(text)
        twiml = (f'<Response><Say voice="alice">{say}</Say><Pause length="1"/>'
                 f'<Say voice="alice">Repeating. {say}</Say></Response>')
        return self._post("Calls", {"To": to, "Twiml": twiml})

    def sms(self, to: str, text: str) -> str:
        return self._post("Messages", {"To": to, "Body": text})


class Ntfy:
    """Free push via ntfy.sh (or NTFY_SERVER). The text goes in the body, never in a URL."""

    @staticmethod
    def server() -> str:
        url = os.environ.get("NTFY_SERVER", "").strip().rstrip("/")
        return url if url.startswith(("https://", "http://")) else "https://ntfy.sh"

    def push(self, topic: str, text: str, level: int, who: str) -> str:
        title = f"NightWatch: {({1: 'HEADS-UP', 2: 'URGENT', 3: 'EMERGENCY'}).get(level, 'ALERT')} {who}".strip()
        req = urllib.request.Request(
            f"{self.server()}/{urllib.parse.quote(topic)}", text.encode("utf-8"),
            {"Title": title.encode("ascii", "replace").decode(),   # HTTP headers are latin-1 only
             "Priority": "urgent" if level >= 3 else "high",
             "Tags": "rotating_light" if level >= 3 else "warning",
             "Content-Type": "text/plain; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                if r.status != 200:
                    raise SendFailed(f"ntfy said HTTP {r.status}")
                return "sent"
        except urllib.error.HTTPError as exc:
            raise SendFailed(f"ntfy said HTTP {exc.code}: {exc.reason}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
            raise SendFailed("no internet: staff phones still alerted") from None


# The text and recipient arrive as argv: user text is never part of the script source.
IMESSAGE_SCRIPT = """on run argv
    set theText to item 1 of argv
    set theRecipient to item 2 of argv
    tell application "Messages"
        set theService to 1st account whose service type = iMessage
        send theText to participant theRecipient of theService
    end tell
end run"""


class IMessage:
    """Free iMessage from the laptop's own Messages app (macOS only)."""

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin" and MESSAGES_APP.exists() and shutil.which("osascript") is not None

    def send(self, to: str, text: str) -> str:
        if not self.available():
            raise NotConfigured("iMessage needs macOS")
        try:
            done = subprocess.run(["osascript", "-e", IMESSAGE_SCRIPT, text, to], capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            raise SendFailed("Messages didn't answer within 10 s") from None
        except OSError as exc:
            raise SendFailed(f"osascript failed ({type(exc).__name__})") from None
        if done.returncode != 0:
            err = done.stderr or ""
            if "-1743" in err or "Not authorized" in err:
                raise SendFailed("macOS blocked it: allow Automation > Messages for this terminal/app in "
                                 "System Settings > Privacy & Security > Automation")
            if "-1728" in err or "Can't get account" in err or "Can’t get" in err:
                raise SendFailed("Messages isn't signed in to iMessage (or the recipient isn't on iMessage)")
            raise SendFailed("Messages refused: " + re.sub(r"\+?\d{6,}", lambda m: mask(m[0]), err.strip())[-160:])
        return "sent"


class Notifier:
    def __init__(self, path: Path | None, transport: object | None = None, ntfy: object | None = None,
                 imessage: object | None = None) -> None:
        self.path = path
        self.transport = transport or Twilio()
        self.ntfy = ntfy or Ntfy()
        self.imessage = imessage or IMessage()
        self._tested: dict[str, float] = {}
        self.live = False                 # dry run until the laptop turns it on; never saved
        self._lock = threading.Lock()
        self._sent: deque[float] = deque()  # real sends in the last hour
        self.contacts = self._load()

    def _load(self) -> list[dict[str, object]]:
        if self.path is None:
            return [dict(c) for c in DEFAULT_CONTACTS]
        try:
            return validate_contacts(json.loads(self.path.read_text()).get("contacts"))
        except (OSError, ValueError, AttributeError):
            contacts = [dict(c) for c in DEFAULT_CONTACTS]
            if not self.path.exists():
                self._save(contacts)
            return contacts

    def _save(self, contacts: list[dict[str, object]]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"_note": "Local only (git-ignored data/). Edit in /settings/ or POST /api/contacts.",
                                   "contacts": contacts}, indent=2))
        os.replace(tmp, self.path)

    def configured(self) -> bool:
        return bool(getattr(self.transport, "configured", lambda: False)())

    def available(self) -> dict[str, bool]:
        """Which channels can really send from this laptop right now (push also needs internet)."""
        twilio = self.configured()
        return {"call": twilio, "sms": twilio, "push": True,
                "imessage": bool(getattr(self.imessage, "available", lambda: False)())}

    def view(self, local: bool) -> dict[str, object]:
        with self._lock:
            contacts = [dict(c) if local else dict(c, phone=mask_any(str(c.get("phone") or "")),
                                                    topic=mask_any(str(c.get("topic") or "")),
                                                    imessage=mask_any(str(c.get("imessage") or "")))
                        for c in self.contacts]
            return {"live": self.live, "twilio_configured": self.configured(), "contacts": contacts,
                    "available": self.available(), "suggested_topic": suggest_topic(),
                    "roles": list(ROLES), "channels": list(CHANNELS),
                    "limits": {"fanout_gap_s": FANOUT_GAP_S, "per_fanout": MAX_REAL_PER_FANOUT, "per_hour": MAX_REAL_PER_HOUR}}

    def update(self, contacts: object | None = None, live: bool | None = None) -> None:
        clean = validate_contacts(contacts) if contacts is not None else None
        with self._lock:
            if clean is not None:
                self._save(clean)
                self.contacts = clean
            if live is not None:
                self.live = live

    def contacts_for(self, resident: str | None) -> list[dict[str, object]]:
        """Who to tell about an alert: family only for their own resident; resident lists scope others too."""
        with self._lock:
            out = []
            for c in self.contacts:
                scoped = c["residents"]
                if c["role"] == "family" and not resident:
                    continue
                if scoped and resident not in scoped:  # type: ignore[operator]
                    continue
                out.append(dict(c))
            return out

    def take_budget(self) -> bool:
        """One real send against the hourly cap."""
        with self._lock:
            now = time.time()
            while self._sent and now - self._sent[0] > 3600:
                self._sent.popleft()
            if len(self._sent) >= MAX_REAL_PER_HOUR:
                return False
            self._sent.append(now)
            return True

    def unavailable(self, ch: str) -> str | None:
        """Why a channel can't really send here, or None."""
        if ch in ("call", "sms") and not self.configured():
            return "Twilio not configured"
        if ch == "imessage" and not self.available()["imessage"]:
            return "iMessage needs macOS"
        return None

    def tested_recently(self, cid: str, gap: float = 10) -> bool:
        with self._lock:
            now = time.time()
            if now - self._tested.get(cid, 0) < gap:
                return True
            self._tested[cid] = now
            return False

    def send(self, channel: str, to: str, text: str, level: int = 3, who: str = "") -> tuple[str, str]:
        """Blocking (run in a thread). Returns (status, detail)."""
        try:
            if channel == "push":
                return "sent", str(self.ntfy.push(to, text, level, who))  # type: ignore[attr-defined]
            if channel == "imessage":
                return "sent", str(self.imessage.send(to, text))  # type: ignore[attr-defined]
            fn = self.transport.call if channel == "call" else self.transport.sms  # type: ignore[attr-defined]
            twilio_status = fn(to, text)
            return ("calling" if channel == "call" else "sent"), str(twilio_status)
        except NotConfigured as exc:
            return "simulated", str(exc)
        except Offline as exc:
            return "simulated", str(exc)
        except SendFailed as exc:
            return "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - never let a send crash anything
            return "failed", type(exc).__name__
