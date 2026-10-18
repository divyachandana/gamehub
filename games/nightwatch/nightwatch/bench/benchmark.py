"""Benchmark NightWatch's ExecuTorch models: Hexagon NPU (QNN) vs CPU (XNNPACK).

Two models, auto-detected from the .pte's input shape (or --kind):
- detector: the hallway-camera person detector (SSDlite320-MobileNetV3), input (1,3,320,320).
  The main NPU workload. --rate is frames/s per camera (the camera page sends 5), --cameras N.
- pattern: the ~850-parameter normal-pattern model, input (1,8). --rate 4 = 4 residents, each
  checked once per second.

    python nightwatch/bench/benchmark.py \
        --pte models/person_ssdlite_qnn.pte:NPU --pte models/person_ssdlite_xnnpack.pte:CPU --rate max
    python nightwatch/bench/benchmark.py --pte models/person_ssdlite_xnnpack.pte:CPU --rate 5 --cameras 2
    python nightwatch/bench/benchmark.py --pte models/normal_pattern_xnnpack.pte:CPU --seconds 10

Each .pte is loaded with executorch.runtime.Runtime exactly like nightwatch/scorer.py and
nightwatch/vision/detector.py, warmed up, then timed on realistic inputs (features.vector()
moments for the pattern model; camera-like frames, or your own JPEGs with --frames, for the
detector). One model instance serves every camera, as in the server, so the load is
rate x cameras frames/s, run in sequence.

Reports p50/p95/p99 latency, throughput and, when the battery gauge can be read, average
WHOLE-LAPTOP power while running vs an idle baseline measured first:
    energy per check/frame = (W_running - W_idle) / checks_per_second
It also runs one fixed set of inputs through every .pte and reports the max difference vs the
first one, to show the NPU output matches the CPU. Writes JSON to logs/bench/<timestamp>.json.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import random
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from features import vector  # noqa: E402
import power  # noqa: E402

HR_REST = 64.0
ZONES = ["bed", "room", "corridor", "exit"]
SIZE = 320
PERSON = 1  # COCO "person", as in nightwatch/vision/detector.py


# ---- inputs ---------------------------------------------------------------------------------

def realistic_moments(n: int, seed: int) -> list[list[float]]:
    """A mix of the moments the live engine sees: mostly quiet nights, some wandering, daytime."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        night = rng.random() < 0.7
        hour = rng.choice([22, 23, 0, 1, 2, 3, 4, 5]) + rng.random() if night else rng.uniform(7, 21)
        up = rng.random() < (0.25 if night else 0.8)
        zone = rng.choices(ZONES, weights=[0, 6, 3, 1] if up else [1, 0, 0, 0])[0]
        motion = rng.uniform(0.2, 0.9) if up else rng.uniform(0.0, 0.1)
        hr = None if rng.random() < 0.1 else HR_REST + (rng.uniform(5, 35) if up else rng.uniform(-6, 4))
        minutes_up = rng.uniform(0, 45) if up else 0.0
        out.append(vector(hour, up, motion, hr, HR_REST, zone, minutes_up))
    return out


def fixed_moments() -> list[list[float]]:
    """Deterministic grid for the output check: every zone, in/out of bed, night and day."""
    grid = []
    for hour in (2.0, 4.5, 14.0, 23.0):
        for zone in ZONES:
            for up in (False, True):
                for hr in (None, HR_REST, HR_REST + 30):
                    grid.append(vector(hour, up, 0.6 if up else 0.05, hr, HR_REST, zone, 20 if up else 0))
    return grid


def synthetic_frames(n: int, seed: int):
    """Camera-like frames, normalized to [-1, 1] like detector.preprocess: a dim hallway (floor/wall
    gradient, sensor noise) and, in most frames, a lit person-sized figure at a random spot.
    The detector's compute doesn't depend on content, so these are fine for timing; for the
    output check any fixed input works. Use --frames DIR to time real camera JPEGs instead."""
    import torch

    g = torch.Generator().manual_seed(seed)
    ys = torch.linspace(0, 1, SIZE).view(1, SIZE, 1)
    frames = []
    for i in range(n):
        brightness = 0.15 + 0.5 * float(torch.rand(1, generator=g))       # night IR .. lit corridor
        img = (brightness * (0.6 + 0.4 * ys)).expand(3, SIZE, SIZE).clone()
        if i % 4 != 0:                                                      # 3 of 4 frames have a figure
            h = int(SIZE * (0.35 + 0.4 * float(torch.rand(1, generator=g))))
            w = h // 3
            x0 = int((SIZE - w) * float(torch.rand(1, generator=g)))
            y0 = SIZE - h - int(0.1 * SIZE * float(torch.rand(1, generator=g)))
            img[:, y0:y0 + h, x0:x0 + w] = torch.rand(3, 1, 1, generator=g) * 0.6 + 0.3
            hy, hx = y0 - w // 2, x0 + w // 4                               # head
            img[:, max(hy, 0):max(hy, 0) + w // 2, hx:hx + w // 2] = 0.8
        img = (img + 0.03 * torch.randn(3, SIZE, SIZE, generator=g)).clamp(0, 1)
        frames.append(img.sub(0.5).div(0.5).unsqueeze(0).contiguous())
    return frames


def jpeg_frames(folder: Path):
    """Real frames, preprocessed exactly like the server (PersonDetector.preprocess, .contiguous())."""
    from vision.detector import PersonDetector

    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not files:
        sys.exit(f"no .jpg/.png frames in {folder}")
    return [PersonDetector.preprocess(p.read_bytes()) for p in files]


# ---- running --------------------------------------------------------------------------------

def rss_mb() -> float | None:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:  # noqa: BLE001 - optional
        return None


class Backend:
    def __init__(self, pte: Path, label: str) -> None:
        from executorch.runtime import Runtime

        self.pte, self.label = pte, label
        before = rss_mb()
        t = time.perf_counter()
        self._method = Runtime.get().load_program(str(pte)).load_method("forward")
        self.load_ms = (time.perf_counter() - t) * 1000
        after = rss_mb()
        self.load_rss_mb = None if before is None or after is None else after - before

    def input_shape(self) -> tuple[int, ...] | None:
        try:
            return tuple(self._method.metadata.input_tensor_meta(0).sizes())
        except Exception:  # noqa: BLE001 - delegate may not expose it; fall back to the file name
            return None

    def run(self, t):
        return self._method.execute([t])


def detect_kind(b: Backend) -> str:
    shape = b.input_shape()
    if shape is not None:
        return "detector" if len(shape) == 4 else "pattern"
    name = b.pte.name.lower()
    return "detector" if any(k in name for k in ("ssdlite", "person", "detector")) else "pattern"


def compare(kind: str, outs, ref_outs) -> dict:
    """Max abs differences vs the reference backend over the fixed inputs."""
    import torch

    if kind == "pattern":
        dl = max(float((a[0] - r[0]).abs().max()) for a, r in zip(outs, ref_outs))
        ds = max(float((torch.sigmoid(-a[0]) - torch.sigmoid(-r[0])).abs().max()) for a, r in zip(outs, ref_outs))
        return {"max_abs_logit_diff": dl, "max_abs_score_diff": ds}
    dd = max(float((a[0] - r[0]).abs().max()) for a, r in zip(outs, ref_outs))
    dl = max(float((a[1] - r[1]).abs().max()) for a, r in zip(outs, ref_outs))
    ps = lambda o: torch.softmax(o[1][0], dim=-1)[:, PERSON]  # noqa: E731
    dp = max(float((ps(a) - ps(r)).abs().max()) for a, r in zip(outs, ref_outs))
    # Same decision? anchors whose person score is on a different side of 0.5 (detector threshold).
    flips = sum(int(((ps(a) > 0.5) != (ps(r) > 0.5)).sum()) for a, r in zip(outs, ref_outs))
    return {"max_abs_logit_diff": dl, "max_abs_box_delta_diff": dd, "max_abs_person_score_diff": dp,
            "anchors_flipping_0.5_threshold": flips}


def pct(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def timed_run(b: Backend, tensors: list, seconds: float, rate: float | None) -> tuple[list[float], float]:
    """Run for `seconds` at `rate` calls/s (None = back to back); return (latencies ms, wall s)."""
    lat: list[float] = []
    interval = 1.0 / rate if rate else 0.0
    start = time.perf_counter()
    end = start + seconds
    nxt = start
    i = 0
    while True:
        now = time.perf_counter()
        if now >= end:
            break
        if rate:
            if now < nxt:
                time.sleep(min(nxt - now, end - now))
                continue
            nxt = max(nxt + interval, now)  # fell behind: don't burst to catch up
        t = tensors[i % len(tensors)]
        t0 = time.perf_counter()
        b.run(t)
        lat.append((time.perf_counter() - t0) * 1000)
        i += 1
    return lat, time.perf_counter() - start


def parse_pte(spec: str) -> tuple[Path, str]:
    path, _, label = spec.rpartition(":")
    if not path or "/" in label or "\\" in label:  # no label given (or a Windows drive letter)
        path, label = spec, Path(spec).stem
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = ROOT / p
    return p, label


def versions() -> dict:
    from importlib.metadata import version

    out = {"python": platform.python_version()}
    for mod in ("torch", "executorch"):
        try:
            out[mod] = version(mod)
        except Exception:  # noqa: BLE001
            out[mod] = None
    return out


def fmt(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


# ---- main -----------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pte", action="append", required=True, metavar="PATH:LABEL",
                    help="a .pte and a label, e.g. models/person_ssdlite_qnn.pte:NPU (repeatable, same model)")
    ap.add_argument("--kind", choices=["auto", "detector", "pattern"], default="auto")
    ap.add_argument("--seconds", type=float, default=60, help="timed run per backend (default 60)")
    ap.add_argument("--rate", default="max",
                    help="'max' (back to back), or calls/s per camera (detector) / total checks/s (pattern)")
    ap.add_argument("--cameras", type=int, default=1, help="cameras sharing the model; load = rate x cameras")
    ap.add_argument("--frames", type=Path, help="detector: folder of camera JPEGs to time instead of synthetic frames")
    ap.add_argument("--warmup", type=int, default=None, help="untimed runs first (default 500 pattern, 30 detector)")
    ap.add_argument("--idle-seconds", type=float, default=None,
                    help="idle power baseline before the runs (default: same as --seconds, max 60)")
    ap.add_argument("--settle", type=float, default=10,
                    help="pause between phases, excluded from power averages; the gauge lags (default 10 s)")
    ap.add_argument("--no-power", action="store_true", help="skip battery-gauge power")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rate = None if args.rate == "max" else float(args.rate) * args.cameras
    idle_s = args.idle_seconds if args.idle_seconds is not None else min(args.seconds, 60)

    backends = []
    for spec in args.pte:
        path, label = parse_pte(spec)
        if not path.exists():
            sys.exit(f"not found: {path}")
        print(f"loading {label}: {path.name}")
        backends.append(Backend(path, label))
    kinds = {detect_kind(b) for b in backends} if args.kind == "auto" else {args.kind}
    if len(kinds) != 1:
        sys.exit("all --pte files must be the same model (detector or pattern); run them separately")
    kind = kinds.pop()
    warmup = args.warmup if args.warmup is not None else (30 if kind == "detector" else 500)
    unit = "frame" if kind == "detector" else "check"
    print(f"model: {kind}")

    import torch

    # 1. Inputs. Every tensor is .contiguous(): ExecuTorch reads the raw buffer.
    if kind == "detector":
        timing = jpeg_frames(args.frames) if args.frames else synthetic_frames(16, args.seed)
        fixed = synthetic_frames(8, args.seed + 1)
        inputs_desc = f"{len(timing)} {'JPEG' if args.frames else 'synthetic camera-like'} frames, 320x320"
    else:
        timing = [torch.tensor([x], dtype=torch.float32) for x in realistic_moments(2000, args.seed)]
        fixed = [torch.tensor([x], dtype=torch.float32) for x in fixed_moments()]
        inputs_desc = "2000 features.vector() moments"

    # 2. Output check: same fixed inputs through every backend, compare to the first.
    outs = {b.label: [[o.clone() for o in b.run(x)] for x in fixed] for b in backends}
    ref = backends[0].label
    match = {b.label: {"vs": ref, "inputs": len(fixed), **compare(kind, outs[b.label], outs[ref])} for b in backends}

    # 3. Timed runs, with one power sampler covering idle baseline + every backend.
    sampler = None if args.no_power else power.PowerSampler(interval_s=1.0)
    results = []
    idle_w, idle_n = None, 0
    if sampler:
        sampler.__enter__()
    try:
        if sampler:
            print(f"idle baseline: {idle_s:.0f} s - don't touch the laptop")
            time.sleep(args.settle)
            t0 = time.perf_counter()
            time.sleep(idle_s)
            t1 = time.perf_counter()
            idle_w, idle_n = sampler.mean_watts(t0, t1), sampler.count(t0, t1)
        for b in backends:
            for i in range(warmup):
                b.run(timing[i % len(timing)])
            if sampler:
                time.sleep(args.settle)
            print(f"running {b.label} for {args.seconds:.0f} s at rate {args.rate}"
                  + (f" x {args.cameras} cameras" if args.cameras > 1 else ""))
            t0 = time.perf_counter()
            lat, wall = timed_run(b, timing, args.seconds, rate)
            t1 = time.perf_counter()
            lat.sort()
            cps = len(lat) / wall
            run_w = sampler.mean_watts(t0, t1) if sampler else None
            net_w = None if run_w is None or idle_w is None else run_w - idle_w
            results.append({
                "label": b.label, "pte": str(b.pte.relative_to(ROOT) if b.pte.is_relative_to(ROOT) else b.pte),
                "pte_mb": round(b.pte.stat().st_size / 1e6, 3), "load_ms": round(b.load_ms, 1),
                "load_rss_mb": None if b.load_rss_mb is None else round(b.load_rss_mb, 1),
                "calls": len(lat), "seconds": round(wall, 2), "per_s": round(cps, 2),
                "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95), "p99_ms": pct(lat, 99),
                "mean_ms": statistics.fmean(lat), "max_ms": lat[-1],
                "busy_fraction": round(sum(lat) / 1000 / wall, 4),
                "power_samples": sampler.count(t0, t1) if sampler else 0,
                "watts_running": run_w, "watts_idle": idle_w, "watts_above_idle": net_w,
                f"mj_per_{unit}": None if net_w is None else net_w / cps * 1000,
                "output_check": match[b.label],
            })
    finally:
        if sampler:
            sampler.__exit__(None, None, None)

    # 4. Report.
    have_power = idle_w is not None
    load = args.rate if rate is None else f"{args.rate}/s" + (f" x {args.cameras} cameras" if args.cameras > 1 else "")
    diff_key = "max_abs_logit_diff" if kind == "detector" else "max_abs_score_diff"
    diff_name = "max logit diff" if kind == "detector" else "max score diff"
    print()
    print(f"NightWatch {kind} model, batch 1, {args.seconds:.0f} s per backend, load {load}, "
          f"{platform.system()} {platform.machine()}; inputs: {inputs_desc}")
    print()
    print(f"| Backend | p50 ms | p95 ms | p99 ms | {unit}s/s | W running | W idle | W above idle | mJ/{unit} "
          f"| {diff_name} vs {ref} |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    nd = 2 if kind == "detector" else 4
    for r in results:
        print(f"| {r['label']} | {r['p50_ms']:.{nd}f} | {r['p95_ms']:.{nd}f} | {r['p99_ms']:.{nd}f} "
              f"| {r['per_s']:.{1 if kind == 'detector' else 0}f} "
              f"| {fmt(r['watts_running'], 2)} | {fmt(r['watts_idle'], 2)} | {fmt(r['watts_above_idle'], 2)} "
              f"| {fmt(r[f'mj_per_{unit}'], 2 if kind == 'detector' else 4)} | {r['output_check'][diff_key]:.2e} |")
    print()
    notes = []
    if rate is not None:
        for r in results:
            if r["per_s"] < 0.95 * rate:
                notes.append(f"WARNING: {r['label']} reached {r['per_s']:.1f}/s, below the requested {rate:g}/s "
                             "- it can't keep up with this load")
    if sampler is None:
        notes.append("power: skipped (--no-power)")
    elif not have_power:
        notes.append(f"power: battery gauge could not be read ({sampler.error or 'no samples'}) - latency only")
    else:
        notes.append(f"power: {power.LABEL}; idle baseline {idle_w:.2f} W from {idle_n} samples")
        if sampler.saw_ac:
            notes.append("WARNING: AC power was connected during the run - unplug and rerun; watts are not valid")
        if any(r["watts_above_idle"] is not None and r["watts_above_idle"] <= 0 for r in results):
            notes.append("note: a backend measured at or below idle - its draw is inside gauge noise; "
                         "use a longer --seconds or --rate max")
        if args.seconds < 60:
            notes.append("note: runs shorter than 60 s give noisy battery-gauge averages")
    for k, v in match.items():
        if k == ref:
            continue
        extra = (f", person score {v['max_abs_person_score_diff']:.2e}, "
                 f"{v['anchors_flipping_0.5_threshold']} anchors change side of 0.5") if kind == "detector" else ""
        notes.append(f"output check {k} vs {ref} on {v['inputs']} fixed inputs: "
                     f"{diff_name} {v[diff_key]:.2e}{extra}")
    if len(backends) == 1:
        notes.append("output check: only one backend, nothing to compare")
    if any(r["load_rss_mb"] is not None for r in results):
        notes.append("memory (RSS increase when loading each .pte): "
                     + ", ".join(f"{r['label']} {fmt(r['load_rss_mb'], 1)} MB" for r in results))
    for n in notes:
        print(n)

    out_dir = ROOT / "logs" / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"{stamp}-{kind}.json"
    out.write_text(json.dumps({
        "timestamp": stamp, "kind": kind, "inputs": inputs_desc, "requested_rate_per_s": rate,
        "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
                 "processor": platform.processor(), **versions()},
        "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "power_label": power.LABEL if have_power else None,
        "power_on_ac": sampler.saw_ac if sampler else None, "results": results, "notes": notes,
    }, indent=2))
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
