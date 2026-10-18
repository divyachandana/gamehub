"""Decision prompt rendering and model-reply parsing.

The prompt is tiny and identical for every racer so the race measures the model and
the hardware, not prompt differences.
"""

from __future__ import annotations

import json
import re

from san_francisco.errors import InvalidMoveError
from san_francisco.maze import Move
from san_francisco.models import Observation

MAX_WHY_CHARS = 48

SYSTEM_PROMPT = (
    "You drive a taxi through a city maze to reach the exit. "
    "At each junction choose one road. Roads you already drove lead back to places you have "
    "been, so prefer a road you have not driven yet. Reply with JSON only."
)
LOOP_VISITS = 2  # a road driven this often means the car is circling

_JSON_OBJECT = re.compile(r"\{.*?\}", re.DOTALL)
_MOVE_WORD = re.compile(r"\b(left|straight|right|back)\b", re.IGNORECASE)


def describe_goal(goal_dx: int, goal_dy: int) -> str:
    """Turn a grid offset into words, e.g. ``"3 blocks east, 2 blocks north"``."""
    parts = []
    if goal_dx:
        parts.append(f"{abs(goal_dx)} blocks {'east' if goal_dx > 0 else 'west'}")
    if goal_dy:
        parts.append(f"{abs(goal_dy)} blocks {'north' if goal_dy > 0 else 'south'}")
    return ", ".join(parts) if parts else "right here"


def build_messages(obs: Observation) -> list[dict[str, str]]:
    """Render the chat messages for one junction decision.

    Args:
        obs: What the racer can see at the junction.

    Returns:
        OpenAI-style ``[{"role": ..., "content": ...}]`` messages.
    """
    lines = [
        f"You are facing {obs.heading.compass}.",
        f"Exit: {describe_goal(obs.goal_dx, obs.goal_dy)}.",
        "Roads you can take:",
    ]
    for option in obs.options:
        seen = f", already driven {option.visits}x" if option.visits else ", new road"
        lines.append(f"- {option.move.value} (goes {option.compass}{seen})")
    fresh = [option.move.value for option in obs.options if not option.visits]
    if fresh and any(option.visits >= LOOP_VISITS for option in obs.options):
        lines.append(f"You are driving in circles. Take a new road: {', '.join(fresh)}.")
    # Name the choices outside the JSON: some models (Qwen3) copy a "left|right" template
    # verbatim instead of choosing.
    allowed = ", ".join(option.move.value for option in obs.options)
    lines.append(f"Pick exactly one of: {allowed}.")
    lines.append('Reply: {"move": "<your pick>", "why": "<max 6 words>"}')
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_move(reply: str, allowed: list[Move]) -> tuple[Move, str]:
    """Extract the chosen move and a short reason from a model reply.

    Small models often wrap JSON in prose or code fences, so we try strict JSON
    first and fall back to the first move word that is legal here.

    Args:
        reply: Raw text returned by the model.
        allowed: Moves that are legal at this junction.

    Returns:
        The move and a reason trimmed to ``MAX_WHY_CHARS``.

    Raises:
        InvalidMoveError: If no legal move can be found in the reply.
    """
    allowed_values = {move.value for move in allowed}

    for candidate in _JSON_OBJECT.findall(reply):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        move = str(data.get("move", "")).strip().lower()
        if move in allowed_values:
            return Move(move), _clean_why(data.get("why", ""))

    for match in _MOVE_WORD.finditer(reply):
        word = match.group(1).lower()
        if word in allowed_values:
            return Move(word), ""

    raise InvalidMoveError(f"No legal move in reply: {reply[:80]!r}")


def _clean_why(value: object) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= MAX_WHY_CHARS else text[: MAX_WHY_CHARS - 1] + "…"
