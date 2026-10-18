"""Per-resident feature vector for the "is this normal for this resident right now?" model.

Shared by the live engine, the training script and the export check. Order matters.
"""
from __future__ import annotations

import math

FEATURES = [
    "hour_sin", "hour_cos",   # time of day on a circle, so 23:00 and 01:00 are close
    "out_of_bed",             # 0/1 from the bed sensor
    "motion",                 # 0..1 from the wearable (or room sensor)
    "hr_delta",               # (heart rate - resting) / 40, clamped to [-0.5, 1.5]
    "in_corridor",            # 0/1 resident is in a shared corridor / common area
    "near_exit",              # 0/1 resident is in an exit zone
    "minutes_up",             # minutes since getting up / 30, capped at 1
]


def vector(hour: float, out_of_bed: bool, motion: float, hr: float | None, hr_rest: float,
           zone: str, minutes_up: float) -> list[float]:
    """Build the model input from live state."""
    angle = 2 * math.pi * hour / 24
    hr_delta = 0.0 if hr is None else max(-0.5, min((hr - hr_rest) / 40, 1.5))
    return [
        math.sin(angle), math.cos(angle),
        1.0 if out_of_bed else 0.0,
        max(0.0, min(motion, 1.0)),
        hr_delta,
        1.0 if zone in ("corridor", "common") else 0.0,
        1.0 if zone in ("exit", "outside") else 0.0,
        min(minutes_up / 30, 1.0),
    ]
