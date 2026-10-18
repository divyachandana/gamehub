"""Pairing staff phones with a PIN shown on the laptop.

With --lan, anyone on the facility Wi-Fi can reach the server. Resident alerts must not be readable
or acknowledgeable by any device that happens to be on that network, so:

- The laptop itself (localhost) is always trusted: it is the nurse station.
- Any other device must pair once: it enters the 6-digit PIN shown on the laptop, gets a random
  token, and sends it with every API request. Static pages hold no data and stay public.
- Wrong PINs are rate-limited per device (5 tries, then a 60 s lockout), so the PIN can't be guessed.
- Only a hash of each token is kept (logs/paired_devices.json, git-ignored), so phones stay paired
  across server restarts without the file being usable to impersonate them.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path

MAX_TRIES = 5
LOCKOUT_S = 60.0
LOCAL = {"127.0.0.1", "::1", "localhost"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Pairing:
    def __init__(self, store: Path, pin: str | None = None) -> None:
        self.pin = pin or f"{secrets.randbelow(10**6):06d}"
        self._store = store
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        try:
            self._devices: dict[str, dict[str, object]] = json.loads(store.read_text())
        except (OSError, ValueError):
            self._devices = {}

    @staticmethod
    def is_local(ip: str) -> bool:
        return ip in LOCAL or ip.startswith("127.")

    def allowed(self, ip: str, token: str | None) -> bool:
        if self.is_local(ip):
            return True
        if not token:
            return False
        with self._lock:
            return _hash(token) in self._devices

    def pair(self, ip: str, pin: str, name: str) -> tuple[bool, str]:
        """Return (ok, token-or-error)."""
        with self._lock:
            now = time.time()
            recent = [t for t in self._fails.get(ip, []) if now - t < LOCKOUT_S]
            self._fails[ip] = recent
            if len(self._fails) > 1000:   # forget devices whose lockout has expired
                self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < LOCKOUT_S}
                self._fails[ip] = recent
            if len(recent) >= MAX_TRIES:
                return False, f"too many wrong PINs, try again in {int(LOCKOUT_S - (now - recent[0]))} s"
            if not secrets.compare_digest(str(pin).strip(), self.pin):
                recent.append(now)
                return False, "wrong PIN"
            token = secrets.token_urlsafe(24)
            self._devices[_hash(token)] = {"name": name[:40] or "phone", "ip": ip, "paired_at": time.strftime("%H:%M")}
            self._save()
            return True, token

    @property
    def devices(self) -> int:
        with self._lock:
            return len(self._devices)

    def unpair_all(self) -> None:
        with self._lock:
            self._devices = {}
            self._save()

    def _save(self) -> None:
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            self._store.write_text(json.dumps(self._devices, indent=2))
        except OSError:
            pass
