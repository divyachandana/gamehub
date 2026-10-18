"""Forward door/window contact events from an Arduino (USB serial) to the local NightWatch server.

    pip install pyserial
    python nightwatch/bridges/door_serial.py --list                 # find the port
    python nightwatch/bridges/door_serial.py --port COM5            # Windows
    python nightwatch/bridges/door_serial.py --port /dev/cu.usbmodem1101

Expects lines like "DOOR front OPEN" / "DOOR front CLOSED" (see hardware/door_sensor/door_sensor.ino).
No Arduino? Use the simulator page (/sim/): key D toggles the front door.
"""
from __future__ import annotations

import argparse
import json
import urllib.request


def post(server: str, exit_id: str, opened: bool) -> None:
    body = json.dumps({"type": "door", "exit": exit_id, "open": opened}).encode()
    req = urllib.request.Request(server + "/api/sensor", body, {"Content-Type": "application/json"})
    print(urllib.request.urlopen(req, timeout=2).read().decode())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port")
    p.add_argument("--list", action="store_true")
    p.add_argument("--server", default="http://127.0.0.1:8000")
    args = p.parse_args()
    import serial
    from serial.tools import list_ports

    if args.list or not args.port:
        for port in list_ports.comports():
            print(port.device, "-", port.description)
        return
    with serial.Serial(args.port, 115200, timeout=1) as ser:
        print(f"listening on {args.port}")
        while True:
            line = ser.readline().decode(errors="ignore").strip()
            parts = line.split()
            if len(parts) == 3 and parts[0] == "DOOR":
                print(line)
                post(args.server, parts[1], parts[2] == "OPEN")


if __name__ == "__main__":
    main()
