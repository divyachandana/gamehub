"""Frustration scorers: turn one feature vector into a score in [0, 1].

- RuleScorer: hand-weighted logistic score. Works with no model at all, so the demo
  always runs.
- ExecuTorchScorer: the trained model exported to a .pte (XNNPACK for CPU, QNN for the
  Hexagon NPU). Same input, same output.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from features import FEATURES, to_vector

# Weights for the rule-based fallback, in FEATURES order (scaled inputs).
_RULE_WEIGHTS = [0.6, 2.2, 1.2, 0.9, 1.4, 0.8, 1.6, 1.0, 0.9, 0.7]
_RULE_BIAS = -2.6


class RuleScorer:
    """Fallback scorer: weighted sum of the scaled features through a sigmoid."""

    name = "rules (fallback)"
    device = "CPU"

    def score(self, raw: dict[str, float]) -> tuple[float, float]:
        """Return (score, latency_ms)."""
        start = time.perf_counter()
        x = to_vector(raw)
        z = _RULE_BIAS + sum(w * v for w, v in zip(_RULE_WEIGHTS, x))
        return 1 / (1 + math.exp(-z)), (time.perf_counter() - start) * 1000


class ExecuTorchScorer:
    """The trained frustration model, run by the ExecuTorch runtime from a .pte file."""

    def __init__(self, pte_path: Path, device: str) -> None:
        """Load the program once; ``device`` is only a label for the dashboard (NPU/CPU)."""
        import torch  # imported lazily so the rule fallback needs no torch
        from executorch.runtime import Runtime

        self._torch = torch
        program = Runtime.get().load_program(str(pte_path))
        self._method = program.load_method("forward")
        self.name = f"ExecuTorch {pte_path.name}"
        self.device = device

    def score(self, raw: dict[str, float]) -> tuple[float, float]:
        """Return (score, latency_ms); latency covers only the model call."""
        x = self._torch.tensor([to_vector(raw)], dtype=self._torch.float32)
        start = time.perf_counter()
        (logit,) = self._method.execute([x])
        latency_ms = (time.perf_counter() - start) * 1000
        return float(self._torch.sigmoid(logit).flatten()[0]), latency_ms


def build_scorer(pte_path: Path | None, device: str) -> RuleScorer | ExecuTorchScorer:
    """Load the model if given and loadable; otherwise fall back to rules (and say so)."""
    if pte_path is None:
        return RuleScorer()
    try:
        return ExecuTorchScorer(pte_path, device)
    except Exception as exc:  # noqa: BLE001 - any load failure means "use the fallback"
        print(f"[scorer] could not load {pte_path}: {exc!r} -> using rule fallback")
        return RuleScorer()


assert len(_RULE_WEIGHTS) == len(FEATURES)
