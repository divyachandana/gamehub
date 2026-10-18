"""Deciders: the "brains" behind racers.

A decider receives the rendered prompt for a junction and returns the raw model reply
plus timing. The race engine does not care whether the reply came from Gemma on the
Snapdragon NPU (ExecuTorch + QNN), the simulator or the human player.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from inference_rally.errors import DeciderError, DecisionTimeoutError
from inference_rally.maze import Maze, Move
from inference_rally.metrics import TokenMeter
from inference_rally.models import DecisionMetrics, Observation, RacerSpec, SimProfile
from inference_rally.qnn_host import QnnHost, QnnHostError

HUMAN_TIMEOUT_S = 20.0
WARMUP_SEQ_LEN = 64  # "Say ok." plus the chat template, with room for a short reply
# Control text the host streams as plain text: Gemma/Qwen end-of-turn markers and Qwen3's
# thinking block (empty with /no_think).
CONTROL_TEXT = re.compile(r"<think>.*?</think>|<turn\|>|<\|im_end\|>", re.DOTALL)
INITIAL_TOKENS_PER_CHAR = 0.5  # pessimistic until the first real prompt is measured
PROMPT_SAFETY = 1.15
PROMPT_MARGIN_TOKENS = 16
MIN_LEARN_CHARS = 100  # tiny prompts are mostly chat template: a misleading ratio

TokenCallback = Callable[[TokenMeter], None]


@dataclass(frozen=True)
class DecisionResult:
    """Raw reply text and the client-side timing for one decision."""

    text: str
    metrics: DecisionMetrics


class Decider(Protocol):
    """Anything that can answer a junction prompt."""

    async def warmup(self) -> None:
        """Get ready before the countdown (load the model, open connections)."""
        ...

    async def decide(
        self, messages: list[dict[str, str]], obs: Observation, on_token: TokenCallback
    ) -> DecisionResult:
        """Return the model's reply, calling ``on_token`` as tokens stream in."""
        ...


class SimulatedDecider:
    """Fake model with realistic speed and imperfect judgment, for UI work and tests.

    With probability ``accuracy`` it picks a move on a shortest route; otherwise it
    behaves like a plausible small model: heads roughly toward the exit and avoids
    roads it has already driven, which still leads it into dead ends sometimes.
    """

    def __init__(self, profile: SimProfile, maze: Maze, rng: random.Random) -> None:
        """Create a simulated racer.

        Args:
            profile: Speed and accuracy settings.
            maze: Track, used to know which moves are actually optimal.
            rng: Dedicated RNG so each racer's behavior is reproducible.
        """
        self._profile = profile
        self._maze = maze
        self._rng = rng

    async def warmup(self) -> None:
        """Nothing to load for a simulated racer."""

    async def decide(
        self, messages: list[dict[str, str]], obs: Observation, on_token: TokenCallback
    ) -> DecisionResult:
        """Choose a move, then stream a fake reply at the profile's speed."""
        move, why = self._choose(obs)
        text = json.dumps({"move": move.value, "why": why})
        meter = TokenMeter()

        await asyncio.sleep(self._jittered(self._profile.ttft_ms / 1000))
        low, high = self._profile.reply_tokens
        for _ in range(self._rng.randint(low, high)):
            meter.add_tokens(1)
            on_token(meter)
            await asyncio.sleep(self._jittered(1 / self._profile.tps))

        return DecisionResult(text=text, metrics=meter.finish())

    def _choose(self, obs: Observation) -> tuple[Move, str]:
        options = self._maze.options(obs.node, obs.heading)
        optimal = [m for m, dst in options.items() if self._maze.is_optimal_step(obs.node, dst)]
        if optimal and self._rng.random() < self._profile.accuracy:
            move = self._rng.choice(optimal)
            return move, self._reason_toward(obs, move)
        return self._heuristic(obs)

    def _heuristic(self, obs: Observation) -> tuple[Move, str]:
        """Greedy "small model" logic: prefer unexplored roads pointing at the exit."""
        visits = {option.move: option.visits for option in obs.options}
        candidates = [m for m in visits if m is not Move.BACK] or list(visits)

        def score(move: Move) -> float:
            dx, dy = obs.heading.turn(move).vector
            toward = dx * _sign(obs.goal_dx) + dy * _sign(obs.goal_dy)
            return toward - 1.5 * visits[move] + self._rng.uniform(-0.8, 0.8)

        move = max(candidates, key=score)
        if visits[move]:
            return move, "trying another way"
        return move, self._reason_toward(obs, move)

    def _reason_toward(self, obs: Observation, move: Move) -> str:
        compass = obs.heading.turn(move).compass
        templates = (f"exit is {compass}", f"head {compass}", f"{compass} looks open")
        return self._rng.choice(templates)

    def _jittered(self, seconds: float) -> float:
        spread = self._profile.jitter
        return max(0.0, seconds * self._rng.uniform(1 - spread, 1 + spread))


class PromptBudget:
    """Chooses the host's ``seq_len`` so a reply is capped at ``max_tokens`` without overflow.

    The host has no max-new-tokens setting: generation stops at ``seq_len`` (prompt plus
    reply). A ``seq_len`` below the prompt length aborts the host process, so the prompt
    size is estimated from its characters with a ratio learned from the host's own token
    counts, plus a safety margin.
    """

    def __init__(self, max_seq_len: int) -> None:
        """Start with a conservative ratio until the host reports real counts."""
        self._max_seq_len = max_seq_len
        self._tokens_per_char = INITIAL_TOKENS_PER_CHAR

    def seq_len(self, prompt_chars: int, max_tokens: int) -> int:
        """Position ceiling for a prompt of ``prompt_chars`` and a ``max_tokens`` reply."""
        prompt_tokens = math.ceil(prompt_chars * self._tokens_per_char * PROMPT_SAFETY)
        return min(self._max_seq_len, prompt_tokens + PROMPT_MARGIN_TOKENS + max_tokens)

    def learn(self, prompt_chars: int, prompt_tokens: int | None) -> None:
        """Adopt the ratio measured on a real junction prompt."""
        if prompt_tokens and prompt_chars >= MIN_LEARN_CHARS:
            self._tokens_per_char = prompt_tokens / prompt_chars


class QnnDecider:
    """A model on the Snapdragon NPU via ExecuTorch's QNN backend (``qnn_llm_host``).

    Pieces are streamed over the host's named pipe, so TTFT and tokens/sec are timed on
    our side, the same way as for every other racer.
    """

    def __init__(
        self, spec: RacerSpec, host: QnnHost, budget: PromptBudget, prompt_suffix: str = ""
    ) -> None:
        """Create a decider for one racer.

        Args:
            spec: Racer with ``runtime: qnn``.
            host: Shared host process for the racer's model.
            budget: Prompt-size estimator (one per host, so it keeps what it learned).
            prompt_suffix: Appended to every junction prompt (e.g. Qwen3's ``/no_think``).
        """
        self._spec = spec
        self._host = host
        self._budget = budget
        self._prompt_suffix = prompt_suffix

    async def warmup(self) -> None:
        """Load the model onto the NPU (first race only) and run one tiny request."""
        try:
            await asyncio.to_thread(self._host.start)
        except QnnHostError as exc:
            raise DeciderError(f"{self._spec.id}: {exc}") from exc
        await self._complete("", "Say ok.", WARMUP_SEQ_LEN, on_token=None)

    async def decide(
        self, messages: list[dict[str, str]], obs: Observation, on_token: TokenCallback
    ) -> DecisionResult:
        """Stream the model's answer for one junction."""
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        user = "\n".join(m["content"] for m in messages if m["role"] != "system")
        user += self._prompt_suffix
        chars = len(system) + len(user)
        result, prompt_tokens = await self._complete(
            system, user, self._budget.seq_len(chars, self._spec.max_tokens), on_token
        )
        self._budget.learn(chars, prompt_tokens)
        return result

    async def _complete(
        self, system: str, prompt: str, seq_len: int, on_token: TokenCallback | None
    ) -> tuple[DecisionResult, int | None]:
        loop = asyncio.get_running_loop()
        meter = TokenMeter()
        # The NPU can't be interrupted mid-answer: if the race is stopped, the pipe thread
        # keeps streaming until the host finishes. Stop forwarding those tokens to the HUD.
        stopped = False

        def forward() -> None:
            if on_token and not stopped:
                on_token(meter)

        def on_delta(_: str) -> None:
            # Runs on the pipe thread: time the token here, update the HUD on the loop.
            meter.add_tokens(1)
            loop.call_soon_threadsafe(forward)

        try:
            reply = await asyncio.to_thread(self._host.generate, system, prompt, seq_len, on_delta)
        except QnnHostError as exc:
            raise DeciderError(f"{self._spec.id}: {exc}") from exc
        finally:
            stopped = True
        if reply.generated_tokens:
            meter.tokens = reply.generated_tokens  # exact count from the host
        text = CONTROL_TEXT.sub("", reply.text).strip()
        return DecisionResult(text=text, metrics=meter.finish()), reply.prompt_tokens


class HumanDecider:
    """The human player: waits for a move submitted from the browser (arrow keys).

    Reaction time is reported as TTFT/latency so the human sits on the same
    leaderboard as the models.
    """

    def __init__(self, timeout_s: float = HUMAN_TIMEOUT_S) -> None:
        """Create the decider; ``timeout_s`` is how long the player may think."""
        self._timeout_s = timeout_s
        self._pending: asyncio.Future[Move] | None = None
        self._allowed: set[Move] = set()

    @property
    def awaiting(self) -> bool:
        """True while the car is stopped at a junction waiting for the player."""
        return self._pending is not None and not self._pending.done()

    def submit(self, move: Move) -> bool:
        """Deliver the player's move. Returns False if it is not their turn or not legal here."""
        if not self.awaiting or move not in self._allowed:
            return False
        assert self._pending is not None
        self._pending.set_result(move)
        return True

    async def warmup(self) -> None:
        """Humans are always warmed up."""

    async def decide(
        self, messages: list[dict[str, str]], obs: Observation, on_token: TokenCallback
    ) -> DecisionResult:
        """Wait for the player's arrow key; raise if they take too long."""
        self._allowed = {option.move for option in obs.options}
        self._pending = asyncio.get_running_loop().create_future()
        started = time.perf_counter()
        try:
            move = await asyncio.wait_for(self._pending, timeout=self._timeout_s)
        except asyncio.TimeoutError as exc:
            raise DecisionTimeoutError("player did not choose in time") from exc
        finally:
            self._pending = None
        reaction_ms = round((time.perf_counter() - started) * 1000, 1)
        return DecisionResult(
            text=json.dumps({"move": move.value, "why": ""}),
            metrics=DecisionMetrics(ttft_ms=reaction_ms, tps=0, tokens=0, latency_ms=reaction_ms),
        )


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)
