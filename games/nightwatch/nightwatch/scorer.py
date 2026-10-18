"""Scores how unusual a resident's current moment is (0 = normal, 1 = very unusual).

ModelScorer runs the normal-pattern model from a .pte with the ExecuTorch runtime (XNNPACK on
the CPU, or QNN on the Hexagon NPU). RuleScorer is the fallback so the system never stops
watching when a model can't load.
"""
from __future__ import annotations

import math
import time
from pathlib import Path


class RuleScorer:
    name = "rules (fallback)"
    device = "CPU"

    def score(self, x: list[float]) -> tuple[float, float]:
        start = time.perf_counter()
        hour_sin, hour_cos, up, motion, hr_delta, corridor, exit_zone, minutes_up = x
        night = hour_cos > 0.5 or (hour_cos > -0.26 and hour_sin < 0)  # roughly 21:00-05:00
        z = -3.0 + (2.0 * up + 1.5 * corridor + 3.0 * exit_zone + 2.0 * max(hr_delta, 0) + 1.5 * minutes_up) * (1 if night else 0.3)
        return 1 / (1 + math.exp(-z)), (time.perf_counter() - start) * 1000


class ModelScorer:
    def __init__(self, pte: Path, device: str) -> None:
        import torch
        from executorch.runtime import Runtime

        self._torch = torch
        self._method = Runtime.get().load_program(str(pte)).load_method("forward")
        self.name = f"ExecuTorch {pte.name}"
        self.device = device

    def score(self, x: list[float]) -> tuple[float, float]:
        t = self._torch.tensor([x], dtype=self._torch.float32)
        start = time.perf_counter()
        (logit,) = self._method.execute([t])
        ms = (time.perf_counter() - start) * 1000
        return float(self._torch.sigmoid(-logit).flatten()[0]), ms


def build(pte: Path | None, device: str) -> RuleScorer | ModelScorer:
    if pte is None:
        return RuleScorer()
    try:
        return ModelScorer(pte, device)
    except Exception as exc:  # noqa: BLE001 - any failure: keep watching with rules
        print(f"[scorer] could not load {pte}: {exc!r} -> rule fallback")
        return RuleScorer()
