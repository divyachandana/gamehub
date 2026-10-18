"""Send watch heart rate to the local server.

    python server/hr_bridge.py --scan                 # list nearby Bluetooth devices
    python server/hr_bridge.py --ble "Charge 6"       # live: any device broadcasting the standard
                                                      # Bluetooth Heart Rate service (0x180D)
    python server/hr_bridge.py --replay data/hr.csv   # replay a recorded trace (columns: t,bpm)
    python server/hr_bridge.py --simulate             # fake resting-then-rising HR for testing

Fitbit / Apple Watch do not stream to a PC by default. Use a model or app that broadcasts the
standard heart-rate service (e.g. a Fitbit with HR sharing, or a broadcast app on Apple Watch).
Anything replayed or simulated is labeled as such on the dashboard; say so in the demo.
Live mode needs: pip install bleak
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import random
import time
import urllib.request
from pathlib import Path

HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def send(url: str, bpm: float, source: str) -> None:
    body = json.dumps({"bpm": bpm, "source": source}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except OSError as exc:
        print(f"server not reachable: {exc}")


def parse_hr(data: bytearray) -> int:
    """Heart Rate Measurement characteristic: flag bit 0 = 16-bit value."""
    return int.from_bytes(data[1:3], "little") if data[0] & 0x01 else data[1]


async def ble(url: str, name: str) -> None:
    from bleak import BleakClient, BleakScanner

    device = await BleakScanner.find_device_by_filter(lambda d, _: bool(d.name and name.lower() in d.name.lower()), timeout=20)
    if device is None:
        raise SystemExit(f"No device named like {name!r}. Run --scan, and make sure HR broadcast is on.")
    print(f"connecting to {device.name} ...")
    async with BleakClient(device) as client:
        def on_hr(_: object, data: bytearray) -> None:
            bpm = parse_hr(data)
            print(f"{bpm} bpm")
            send(url, bpm, f"live: {device.name}")
        await client.start_notify(HR_MEASUREMENT, on_hr)
        while client.is_connected:
            await asyncio.sleep(1)


async def scan() -> None:
    from bleak import BleakScanner

    for d in await BleakScanner.discover(timeout=10):
        print(d.address, d.name)


def replay(url: str, path: Path) -> None:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    start = time.time()
    for r in rows:
        time.sleep(max(0.0, float(r["t"]) - (time.time() - start)))
        send(url, float(r["bpm"]), f"replayed: {path.name}")


def simulate(url: str) -> None:
    start = time.time()
    while True:
        t = time.time() - start
        # 60 s resting around 72 bpm, then a slow climb with noise, like a stressful task.
        bpm = 72 + (0 if t < 60 else 18 * (1 - math.exp(-(t - 60) / 90))) + random.uniform(-2, 2)
        send(url, round(bpm), "simulated")
        time.sleep(1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true")
    g.add_argument("--ble", metavar="NAME")
    g.add_argument("--replay", type=Path)
    g.add_argument("--simulate", action="store_true")
    p.add_argument("--server", default="http://127.0.0.1:8000")
    args = p.parse_args()
    url = args.server + "/api/hr"
    if args.scan:
        asyncio.run(scan())
    elif args.ble:
        asyncio.run(ble(url, args.ble))
    elif args.replay:
        replay(url, args.replay)
    else:
        simulate(url)


if __name__ == "__main__":
    main()
