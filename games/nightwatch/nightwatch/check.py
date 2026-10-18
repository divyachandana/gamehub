"""One-command health check for the demo laptop. Run it first; it tells you exactly what works.

    python nightwatch/check.py
    python nightwatch/check.py --npu      # also check the QNN (.pte for the Hexagon NPU) models

Checks: Python version and CPU architecture, imports (torch, executorch, pillow, optional
bleak/pyserial), each model file loads in the ExecuTorch runtime and runs once (with timing),
port 8000 is free, the web pages and the assets they link exist, logs/ is writable and tonight's
saved state (logs/night_state.json) is readable, and prints the command to start the server with
whatever works.
"""
from __future__ import annotations

import argparse
import io
import json
import platform
import re
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
WEB = ROOT / "nightwatch" / "web"
LOGS = ROOT / "logs"
PAGES = ("home", "dashboard", "staff", "sim", "camera")          # the demo needs these
EXTRA_PAGES = ("perf", "report", "settings", "story", "wall", "family", "help")           # nice to have
ASSETS = ("auth.js", "nw.js", "theme.css")
OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "


def line(status: str, what: str, detail: str = "") -> None:
    print(f"[{status}] {what}{(': ' + detail) if detail else ''}")


def try_import(name: str, optional: bool = False) -> bool:
    try:
        mod = __import__(name)
        line(OK, f"import {name}", getattr(mod, "__version__", ""))
        return True
    except Exception as exc:  # noqa: BLE001
        line(WARN if optional else FAIL, f"import {name}", f"{exc!r}")
        return False


def run_model(path: Path, make_input: object) -> float | None:
    if not path.exists():
        line(WARN, path.name, "not found")
        return None
    try:
        from executorch.runtime import Runtime

        method = Runtime.get().load_program(str(path)).load_method("forward")
        x = make_input()  # type: ignore[operator]
        method.execute([x])  # warm-up
        start = time.perf_counter()
        for _ in range(20):
            method.execute([x])
        ms = (time.perf_counter() - start) * 1000 / 20
        line(OK, path.name, f"{ms:.3f} ms per run")
        return ms
    except Exception as exc:  # noqa: BLE001
        line(FAIL, path.name, f"{exc!r}")
        return None


def check_web() -> None:
    for name in ASSETS:
        f = WEB / "assets" / name
        line(OK if f.exists() else FAIL, f"web/assets/{name}", "" if f.exists() else "missing")
    for page in PAGES + EXTRA_PAGES:
        f = WEB / page / "index.html"
        if not f.exists():
            line(FAIL if page in PAGES else WARN, f"/{page}/", "page missing")
            continue
        html = f.read_text(encoding="utf-8", errors="replace")
        missing = sorted({ref for ref in re.findall(r'(?:src|href)="(/assets/[^"?#]+)', html)
                          if not (WEB / ref.lstrip("/")).exists()})
        line(FAIL if missing else OK, f"/{page}/", f"links missing {', '.join(missing)}" if missing else "")


def check_state() -> None:
    try:
        LOGS.mkdir(exist_ok=True)
        probe = LOGS / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        line(OK, "logs/ writable", "night state and nightwatch.log are saved here")
    except OSError as exc:
        line(FAIL, "logs/ writable", f"{exc!r}: the server can't save state across a restart")
    state = LOGS / "night_state.json"
    if not state.exists():
        line(OK, "night state", "none saved yet (the server starts a clean night)")
        return
    try:
        data = json.loads(state.read_text())
        sys.path.insert(0, str(ROOT / "nightwatch"))
        from engine import Engine

        ok, why = Engine.restorable(data)
        open_alerts = sum(1 for a in data.get("alerts", []) if not a.get("resolved"))
        line(OK, "night state", f"{len(data.get('alerts', []))} alerts ({open_alerts} open), "
             + (f"will be restored on start ({why})" if ok else f"won't be restored: {why}")
             + "; use --fresh for a clean night")
    except Exception as exc:  # noqa: BLE001
        line(WARN, "night state", f"{state.name} unreadable ({exc!r}); the server will start a clean night")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--npu", action="store_true")
    args = p.parse_args()

    print(f"Python {platform.python_version()} on {platform.system()} {platform.machine()} "
          f"(interpreter built for {platform.architecture()[0]})")
    if sys.version_info < (3, 10):
        line(FAIL, "Python", "need 3.10+")
    if platform.system() == "Windows" and platform.machine().lower() in ("arm64", "aarch64"):
        line(OK, "Windows on ARM", "if executorch won't install, try an x64 Python (runs under emulation)")

    have_torch = try_import("torch")
    have_et = have_torch and try_import("executorch")
    try_import("PIL")
    try_import("bleak", optional=True)
    try_import("serial", optional=True)

    results: dict[str, float | None] = {}
    if have_et:
        import torch

        normal = lambda: torch.zeros(1, 8)  # noqa: E731
        frame = lambda: (torch.rand(1, 3, 320, 320) * 2 - 1).contiguous()  # noqa: E731
        results["pattern_cpu"] = run_model(MODELS / "normal_pattern_xnnpack.pte", normal)
        results["camera_cpu"] = run_model(MODELS / "person_ssdlite_xnnpack.pte", frame)
        results["camera_cpu_int8"] = run_model(MODELS / "person_ssdlite_xnnpack_int8.pte", frame)
        if args.npu:
            results["pattern_npu"] = run_model(MODELS / "normal_pattern_qnn.pte", normal)
            results["camera_npu"] = run_model(MODELS / "person_ssdlite_qnn.pte", frame) \
                or run_model(MODELS / "person_ssdlite_qnn_int8.pte", frame)
        if not (MODELS / "person_ssdlite_anchors.pt").exists():
            line(FAIL, "person_ssdlite_anchors.pt", "missing; git pull")

        try:
            from PIL import Image

            sys.path.insert(0, str(ROOT / "nightwatch"))
            from vision.detector import PersonDetector

            det = PersonDetector(MODELS / "person_ssdlite_xnnpack.pte", MODELS / "person_ssdlite_anchors.pt", "CPU")
            buf = io.BytesIO()
            Image.new("RGB", (320, 320), (90, 90, 90)).save(buf, "JPEG")
            boxes, ms = det.detect(buf.getvalue())
            line(OK, "camera pipeline (jpeg -> boxes)", f"{len(boxes)} people in a blank frame, {ms:.1f} ms")
        except Exception as exc:  # noqa: BLE001
            line(FAIL, "camera pipeline", f"{exc!r}")

    check_web()
    check_state()

    with socket.socket() as s:
        free = s.connect_ex(("127.0.0.1", 8000)) != 0
    line(OK if free else WARN, "port 8000", "free" if free else "in use (server already running? or use --port 8001)")

    npu = args.npu and results.get("camera_npu") is not None
    pattern = "normal_pattern_qnn.pte --device NPU" if args.npu and results.get("pattern_npu") else \
        "normal_pattern_xnnpack.pte --device CPU" if results.get("pattern_cpu") else None
    cmd = ["python nightwatch/server.py --lan --l1-timeout 20 --ack-timeout 15"]
    if pattern:
        cmd.append(f"--model models/{pattern}")
    if npu:
        cmd.append("--camera-model models/person_ssdlite_qnn.pte --camera-device NPU")
    elif results.get("camera_cpu"):
        cmd.append("--camera-model models/person_ssdlite_xnnpack.pte --camera-device CPU")
    print("\nStart the demo with:\n  " + " ".join(cmd))
    print("Then open /dashboard/, /staff/ (phone), /sim/ and /camera/ on port 8000.")


if __name__ == "__main__":
    main()
