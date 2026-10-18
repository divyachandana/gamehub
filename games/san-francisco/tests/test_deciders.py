"""Tests for the NPU and human deciders (no hardware: fake host, fake player)."""

import asyncio
import time
from collections.abc import Callable

import pytest

from san_francisco.deciders import HumanDecider, PromptBudget, QnnDecider
from san_francisco.errors import DeciderError, DecisionTimeoutError
from san_francisco.maze import Heading, Move
from san_francisco.models import Observation, OptionView, RacerSpec
from san_francisco.qnn_host import HostReply, QnnHostError

OBS = Observation(
    node=(1, 1),
    heading=Heading.NORTH,
    goal_dx=3,
    goal_dy=2,
    options=[
        OptionView(move=Move.LEFT, compass="west", visits=0),
        OptionView(move=Move.RIGHT, compass="east", visits=0),
    ],
)
SPEC = RacerSpec(
    id="npu",
    name="Gemma",
    device="NPU",
    runtime="qnn",
    color="#1D9E75",
    qnn_model="gemma",
)


class FakeHost:
    """Mimics ``QnnHost``: streams pieces through ``on_delta`` like the named pipe does."""

    def __init__(self, pieces: list[str], prompt_tokens: int = 100, fail: bool = False) -> None:
        self.pieces, self.prompt_tokens, self.fail = pieces, prompt_tokens, fail
        self.calls: list[tuple[str, str, int]] = []

    def start(self) -> None:
        if self.fail:
            raise QnnHostError("host exited")

    def generate(
        self, system_prompt: str, prompt: str, seq_len: int, on_delta: Callable[[str], None]
    ) -> HostReply:
        self.calls.append((system_prompt, prompt, seq_len))
        if self.fail:
            raise QnnHostError("pipe broken")
        for piece in self.pieces:
            time.sleep(0.001)
            on_delta(piece)
        return HostReply("".join(self.pieces), self.prompt_tokens, len(self.pieces))


def _decider(host: FakeHost) -> QnnDecider:
    return QnnDecider(SPEC, host, PromptBudget(max_seq_len=3800))  # type: ignore[arg-type]


MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "u" * 300}]


def test_qnn_decider_streams_and_times_reply() -> None:
    host = FakeHost(['{"move"', ': "right"', ', "why": "east"}', "<turn|>"])
    seen: list[int] = []

    async def scenario() -> object:
        result = await _decider(host).decide(MESSAGES, OBS, lambda m: seen.append(m.tokens))
        await asyncio.sleep(0)  # let the loop run the last scheduled HUD update
        return result

    result = asyncio.run(scenario())
    assert result.text == '{"move": "right", "why": "east"}'  # type: ignore[attr-defined]
    assert seen == [1, 2, 3, 4]  # live updates while streaming
    assert result.metrics.tokens == 4  # type: ignore[attr-defined]
    assert host.calls[0][0] == "sys" and host.calls[0][1] == "u" * 300


def test_qnn_decider_adds_suffix_and_strips_qwen_control_text() -> None:
    host = FakeHost(["<think>\n\n</think>\n\n", '{"move": "left", "why": "west"}', "<|im_end|>"])
    decider = QnnDecider(SPEC, host, PromptBudget(3800), " /no_think")  # type: ignore[arg-type]
    result = asyncio.run(decider.decide(MESSAGES, OBS, lambda _: None))
    assert result.text == '{"move": "left", "why": "west"}'
    assert host.calls[0][1].endswith(" /no_think")


def test_qnn_decider_reports_offline_host() -> None:
    with pytest.raises(DeciderError):
        asyncio.run(_decider(FakeHost([], fail=True)).warmup())


def test_prompt_budget_learns_ratio_and_never_starves_prompt() -> None:
    budget = PromptBudget(max_seq_len=3800)
    first = budget.seq_len(400, 48)
    assert first >= 400 * 0.5 + 48  # pessimistic before any measurement
    budget.learn(400, 120)
    tight = budget.seq_len(400, 48)
    assert 120 + 48 <= tight < first
    budget.learn(10, 50)  # tiny warmup prompts are ignored
    assert budget.seq_len(400, 48) == tight
    assert PromptBudget(max_seq_len=100).seq_len(4000, 48) == 100


def test_human_decider_accepts_legal_move_on_their_turn() -> None:
    async def scenario() -> tuple[bool, bool, object]:
        human = HumanDecider(timeout_s=2)
        task = asyncio.create_task(human.decide([], OBS, lambda _: None))
        await asyncio.sleep(0.01)
        illegal = human.submit(Move.STRAIGHT)  # not an option at this junction
        legal = human.submit(Move.LEFT)
        return illegal, legal, await task

    illegal, legal, result = asyncio.run(scenario())
    assert illegal is False and legal is True
    assert '"left"' in result.text  # type: ignore[attr-defined]
    assert result.metrics.tokens == 0  # type: ignore[attr-defined]


def test_human_decider_rejects_moves_when_not_waiting() -> None:
    assert HumanDecider().submit(Move.LEFT) is False


def test_human_decider_times_out() -> None:
    with pytest.raises(DecisionTimeoutError):
        asyncio.run(HumanDecider(timeout_s=0.05).decide([], OBS, lambda _: None))


def test_qnn_decider_stops_live_updates_when_the_race_is_stopped() -> None:
    host = FakeHost(['{"move"', ': "right"'] + [" ."] * 200)  # ~0.2 s+ of streaming
    seen: list[int] = []

    async def scenario() -> None:
        task = asyncio.create_task(
            _decider(host).decide(MESSAGES, OBS, lambda m: seen.append(m.tokens))
        )
        while not seen:
            await asyncio.sleep(0.005)
        task.cancel()  # STOP: the race task is cancelled mid-answer
        with pytest.raises(asyncio.CancelledError):
            await task
        stopped_at = len(seen)
        await asyncio.sleep(0.4)  # the NPU thread keeps streaming the rest of its answer
        assert len(seen) == stopped_at

    asyncio.run(scenario())
