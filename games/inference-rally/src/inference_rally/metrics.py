"""Client-side inference timing.

Every decider uses a :class:`TokenMeter`, so TTFT and tokens/sec are measured the same
way for every model on the NPU and for the simulator — host-reported timings are not
comparable across models and processes.
"""

from __future__ import annotations

import time

from inference_rally.models import DecisionMetrics


class TokenMeter:
    """Stopwatch for one streamed model reply."""

    def __init__(self) -> None:
        """Start timing immediately (the request is about to be sent)."""
        self._started = time.perf_counter()
        self._first_token: float | None = None
        self._last_token: float | None = None
        self.tokens = 0

    def add_tokens(self, count: int = 1) -> None:
        """Record ``count`` newly streamed tokens."""
        now = time.perf_counter()
        if self._first_token is None:
            self._first_token = now
        self._last_token = now
        self.tokens += count

    @property
    def ttft_ms(self) -> float | None:
        """Time to first token in milliseconds, or ``None`` while still waiting."""
        if self._first_token is None:
            return None
        return (self._first_token - self._started) * 1000

    @property
    def tps(self) -> float:
        """Generation speed: tokens after the first one, divided by the streaming time.

        The first token is excluded because its time is already counted in TTFT
        (prompt processing), which would otherwise drag the rate down.
        """
        if self._first_token is None or self._last_token is None or self.tokens < 2:
            return 0.0
        elapsed = self._last_token - self._first_token
        return (self.tokens - 1) / elapsed if elapsed > 0 else 0.0

    def finish(self) -> DecisionMetrics:
        """Freeze the measurements for this reply."""
        latency_ms = (time.perf_counter() - self._started) * 1000
        return DecisionMetrics(
            ttft_ms=round(self.ttft_ms or latency_ms, 1),
            tps=round(self.tps, 2),
            tokens=self.tokens,
            latency_ms=round(latency_ms, 1),
        )
