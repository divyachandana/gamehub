"""Whole-laptop power from the battery gauge. No admin rights, no vendor tools.

Read this before quoting a number:
- Run on battery (unplugged), or the gauge reports charging current and the numbers are
  meaningless. The sampler records whether it ever saw AC power and the benchmark warns.
- This is WHOLE-SYSTEM power (screen, Wi-Fi, background apps, CPU, NPU), not NPU-only power.
  Only the difference against an idle baseline taken in the same conditions says anything
  about the model, so keep the same screen brightness and background apps for every run.
- Battery gauges update slowly (often every few seconds, some every 10-30 s), so short runs
  give noisy averages. Use 60 s or more per backend for numbers you present.

- Windows: root\\wmi BatteryStatus.DischargeRate (mW), read by one long-lived PowerShell
  process so sampling does not spawn a new process (and burn CPU) every second.
- macOS: ioreg AppleSmartBattery InstantAmperage (mA, negative on discharge) x Voltage (mV).
"""
from __future__ import annotations

import platform
import re
import statistics
import subprocess
import threading
import time

LABEL = "whole-laptop power from the battery gauge (not NPU-only)"


def _read_mac() -> tuple[float | None, bool]:
    """Return (watts drawn from the battery, on_ac)."""
    out = subprocess.run(["ioreg", "-rn", "AppleSmartBattery"], capture_output=True, text=True).stdout
    amp = re.search(r'"InstantAmperage" = (\d+)', out)
    volt = re.search(r'"Voltage" = (\d+)', out)
    ac = re.search(r'"ExternalConnected" = (Yes|No)', out)
    if not (amp and volt):
        return None, False
    ma = int(amp.group(1))
    if ma >= 2**63:  # unsigned wrap of a negative (discharging) current
        ma -= 2**64
    return max(0.0, -ma) * int(volt.group(1)) / 1e6, bool(ac and ac.group(1) == "Yes")


_WIN_LOOP = (
    "while ($true) {"
    " $b = Get-CimInstance -Namespace root\\wmi -ClassName BatteryStatus | Select-Object -First 1;"
    " if ($b) { Write-Output \"$($b.DischargeRate) $($b.PowerOnline)\" } else { Write-Output 'none' };"
    " [Console]::Out.Flush(); Start-Sleep -Milliseconds %d }"
)


class PowerSampler:
    """Samples battery power in a background thread: `with PowerSampler() as p: ...`."""

    def __init__(self, interval_s: float = 1.0):
        self.interval_s = interval_s
        self.samples: list[tuple[float, float]] = []
        self.saw_ac = False
        self.error: str | None = None
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _add(self, watts: float | None, on_ac: bool) -> None:
        self.saw_ac |= on_ac
        if watts is not None:
            self.samples.append((time.perf_counter(), watts))

    def _run(self) -> None:
        try:
            system = platform.system()
            if system == "Windows":
                self._run_windows()
            elif system == "Darwin":
                while not self._stop.is_set():
                    self._add(*_read_mac())
                    self._stop.wait(self.interval_s)
            else:
                self.error = f"no battery reader for {system}"
        except Exception as exc:  # noqa: BLE001 - power is optional; never break the benchmark
            self.error = repr(exc)

    def _run_windows(self) -> None:
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", _WIN_LOOP % int(self.interval_s * 1000)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            parts = line.split()
            if len(parts) >= 2:
                try:
                    self._add(float(parts[0]) / 1000.0, parts[1].lower() == "true")
                except ValueError:
                    pass

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._proc is not None:
            self._proc.kill()
        self._thread.join(timeout=5)

    def mean_watts(self, t0: float | None = None, t1: float | None = None) -> float | None:
        vals = [w for t, w in self.samples if (t0 is None or t >= t0) and (t1 is None or t <= t1)]
        return round(statistics.fmean(vals), 3) if vals else None

    def count(self, t0: float | None = None, t1: float | None = None) -> int:
        return sum(1 for t, _ in self.samples if (t0 is None or t >= t0) and (t1 is None or t <= t1))

