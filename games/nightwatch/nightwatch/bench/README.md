# NightWatch benchmark: Hexagon NPU vs CPU

`benchmark.py` runs NightWatch's ExecuTorch models from one or more `.pte` files, loaded the same way the
server loads them (`executorch.runtime.Runtime`), and compares backends. It works out which model a
`.pte` is from its input shape (override with `--kind detector|pattern`):

| Model | Input | What a call is | Realistic load |
|---|---|---|---|
| **Person detector** (SSDlite320-MobileNetV3, COCO), the main NPU workload | (1,3,320,320) frame in [-1,1] | one camera frame | `--rate 5` per camera (what `/camera/` sends), `--cameras N` |
| Normal-pattern model (~850 parameters) | (1,8) features | one resident check | `--rate 4` (4 residents, once per second) |

Per backend it reports:

- p50 / p95 / p99 latency (ms per frame or check) and throughput (frames/s or checks/s)
- average **whole-laptop** power while running vs an idle baseline measured first, and
  energy per call = (W running − W idle) / calls per second, if the battery gauge can be read
- an output check: fixed inputs through every `.pte`, max abs difference vs the first one listed
  (detector: class logits, box deltas, person score, and how many anchors cross the 0.5 detection
  threshold; pattern model: logit and score)
- memory: process RSS increase when loading each `.pte` (if `psutil` is installed)

It prints a markdown table and writes everything (host, versions, args, results, warnings) to
`logs/bench/<timestamp>-<model>.json` (git-ignored).

Detector inputs are synthetic camera-like frames (dim hallway, sensor noise, a person-sized figure in most
frames), built as contiguous tensors like `nightwatch/vision/detector.py` (a permuted view gives wrong
results in the ExecuTorch runtime). A CNN's compute doesn't depend on what's in the frame, so these are fine
for timing. To time real frames instead: `--frames path/to/jpegs/` (preprocessed exactly like the server).

## On the Snapdragon X Elite laptop (Windows on ARM)

Before you start:

1. **Unplug the charger.** The battery gauge only measures power on battery; the script warns if it sees AC.
2. Fixed screen brightness, close other apps (including the NightWatch server and the browser camera page),
   pause OneDrive / Windows Update, and use the same power mode for every run. Write down which one.
3. Don't touch the laptop during the idle baseline or the runs.

From the repo root. The first `--pte` is the reference for the output check.

**Person detector (main workload):**

```bash
python nightwatch/bench/benchmark.py --pte models/person_ssdlite_qnn.pte:NPU --pte models/person_ssdlite_xnnpack.pte:CPU --rate max
```

```bash
python nightwatch/bench/benchmark.py --pte models/person_ssdlite_qnn.pte:NPU --pte models/person_ssdlite_xnnpack.pte:CPU --rate 5 --cameras 2
```

**Normal-pattern model:**

```bash
python nightwatch/bench/benchmark.py --pte models/normal_pattern_qnn.pte:NPU --pte models/normal_pattern_xnnpack.pte:CPU --rate max
```

```bash
python nightwatch/bench/benchmark.py --pte models/normal_pattern_qnn.pte:NPU --pte models/normal_pattern_xnnpack.pte:CPU --rate 4
```

Each run takes about 60 s idle + 2 × (10 s settle + 60 s) ≈ 3.5 min. Before quoting power, **run the
`--rate max` detector command a second time with the two `--pte` flags swapped** (CPU first): if the
"W above idle" numbers move a lot between the two runs, it's gauge noise, not the backend.

Options: `--seconds S` (default 60), `--warmup N` (default 30 frames / 500 checks), `--idle-seconds S`
(default = `--seconds`, max 60), `--settle S` (pause between phases, excluded from power, default 10),
`--frames DIR`, `--no-power`, `--seed`. Only the XNNPACK model on hand? Pass just that one `--pte`.

If a QNN `.pte` fails to load, the QNN runtime libraries on the laptop probably don't match the SDK
version it was compiled with; the script stops with the loader's error rather than guessing.

## What the numbers mean (and don't)

- **Power is whole-laptop power** from the battery gauge (Windows `root\wmi BatteryStatus.DischargeRate`,
  macOS `ioreg`), not NPU power. Screen, Wi-Fi and background apps are all in it. Only "W above idle",
  measured in the same sitting, says anything about the model.
- **The gauge is slow and noisy.** It updates every few seconds (some every 10–30 s) and lags the load.
  On the Mac, two back-to-back 10 s runs of the *same* detector file measured 4.0 W and 14.2 W above idle.
  Use 60 s runs, repeat with the order swapped, and if the numbers don't agree say so instead of quoting one.
- **Latency is the Python `method.execute()` call**, as in the server (preprocessing, decoding and NMS are
  not included). For the tiny normal-pattern model, Python overhead dominates and the NPU adds a CPU→NPU
  round trip per call, so the NPU may not be faster there; the detector is where the NPU should matter.
- `--rate max` keeps the processor busy, the worst case for power. At `--rate 5` it idles between frames,
  so each frame is slower (cold caches, lower clocks: 13 ms vs 5.5 ms on the Mac CPU) but total power is
  much lower; that's the real workload. The script warns if a backend can't keep up with the requested rate.
- The output check compares outputs on identical inputs. fp16 on the NPU differs slightly from fp32 on the
  CPU. "Anchors crossing 0.5" = 0 means the detector makes the same person/no-person calls on those frames.

## Results

### Reference: MacBook (Apple Silicon, macOS), XNNPACK on CPU, measured

Not the target hardware. Shown only so the table shape and magnitudes are clear. Short runs, on battery.

| Model | Load | p50 ms | p95 ms | p99 ms | per s | W above idle | mJ per call |
|---|---|---|---|---|---|---|---|
| Person detector | max, 10 s | 5.50 | 5.84 | 6.15 | 181 frames | 4.0 (same file, rerun: 14.2) | 22 (rerun: 78) |
| Person detector | 5/s × 2 cameras, 10 s | 13.25 | 15.02 | 15.79 | 10 frames | not measured | — |
| Normal-pattern | max, 10 s | 0.0043 | 0.0046 | 0.0049 | 221,827 checks | 1.0 | 0.005 |
| Normal-pattern | 4/s, 5 s | 0.31 | 0.39 | 0.41 | 4 checks | not measured | — |

Detector RSS increase on load: about 14 MB. Output check of a file against itself: 0 difference.

### Snapdragon X Elite, Windows on ARM: fill in on the laptop

Power mode: ______  Brightness: ______  On battery: yes / no  Date: ______

**Person detector**

| Backend | Load | p50 ms | p95 ms | p99 ms | frames/s | W running | W idle | W above idle | mJ/frame | max logit diff | anchors crossing 0.5 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| NPU (QNN, fp16) | max | | | | | | | | | — (reference) | — |
| CPU (XNNPACK, fp32) | max | | | | | | | | | | |
| NPU (QNN, fp16) | 5/s × 2 cameras | | | | | | | | | — (reference) | — |
| CPU (XNNPACK, fp32) | 5/s × 2 cameras | | | | | | | | | | |

**Normal-pattern model**

| Backend | Load | p50 ms | p95 ms | p99 ms | checks/s | W running | W idle | W above idle | mJ/check | max score diff |
|---|---|---|---|---|---|---|---|---|---|---|
| NPU (QNN, fp16) | max | | | | | | | | | — (reference) |
| CPU (XNNPACK, fp32) | max | | | | | | | | | |
| NPU (QNN, fp16) | 4/s | | | | | | | | | — (reference) |
| CPU (XNNPACK, fp32) | 4/s | | | | | | | | | |
