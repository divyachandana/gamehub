"""The rogue racer's cheats: spot a useful one, describe it, and let Laya decide.

Code does the bookkeeping Laya can't (geometry, which cheat actually helps); Laya only
judges whether to take it, like a gut call. Cheats that would not help are never offered.

Laya is a text classifier, so situations are described in words, not numbers: with
"cuts the route by 7 blocks" its answers barely moved, with "a huge shortcut" they track
how much the cheat helps. Each cheat has its own question, tuned on sample situations.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from san_francisco.maze import Heading, Maze, Node
from san_francisco.models import RogueSpec

JUMP_QUESTION = "Should the rogue taxi take this cheat now?"
DRONE_QUESTION = "Should the taxi launch the drone now?"
HUGE_GAIN = 6  # road blocks a jump must save to be "a huge shortcut"
GOOD_GAIN = 4
URGENT_RIVAL = 4  # rival this many blocks (or fewer) from the exit: "about to win"
LOST_VISITS = 3  # roads driven this often: "completely lost"

CheatKind = Literal["jump", "drone"]


@dataclass(frozen=True)
class CheatPlan:
    """One cheat on offer at the rogue's current junction."""

    kind: CheatKind
    target: Node | None  # where a jump lands; None for a drone peek
    gain: int  # road blocks saved by a jump; 0 for a drone peek
    state: str  # the situation, in words, for Laya
    question: str  # the yes/no question Laya answers


def plan_cheat(
    maze: Maze,
    node: Node,
    heading: Heading,
    visits: Counter[Node],
    rogue: RogueSpec,
    jumps_left: int,
    drones_left: int,
    rival_distance: int | None,
) -> CheatPlan | None:
    """The most useful cheat at ``node``, or ``None`` if none would help.

    Args:
        maze: The track (the rogue sees the true road distances: that is the cheat).
        node: Junction the rogue is at.
        heading: Direction the rogue is facing.
        visits: How often the rogue has been at each junction.
        rogue: Cheat costs and limits.
        jumps_left: Hedge jumps still available.
        drones_left: Drone peeks still available.
        rival_distance: Road blocks between the leading honest racer and the exit
            (``None`` if every honest racer has finished).
    """
    rival = _rival_text(rival_distance)
    if jumps_left > 0:
        target = min(maze.hedge_neighbors(node), key=maze.distance_to_exit, default=None)
        gain = maze.distance_to_exit(node) - maze.distance_to_exit(target) if target else 0
        if target and gain >= rogue.min_jump_gain:
            last = "This is the last jump." if jumps_left == 1 else f"{jumps_left} jumps left."
            state = (
                f"Rogue taxi in a maze race. Jumping the hedge here is {_shortcut_text(gain)}. "
                f"{last} {rival}"
            )
            return CheatPlan("jump", target, gain, state, JUMP_QUESTION)

    roads = maze.options(node, heading).values()
    circling = max((visits[dst] for dst in roads), default=0)
    if drones_left > 0 and maze.is_junction(node, heading) and circling >= 2:
        lost = (
            "completely lost, driving the same roads again and again"
            if circling >= LOST_VISITS
            else "slightly unsure of the way, but still exploring new roads"
        )
        state = (
            f"Rogue taxi in a maze race. It is {lost}. A drone can show it the right road. {rival}"
        )
        return CheatPlan("drone", None, 0, state, DRONE_QUESTION)
    return None


def _shortcut_text(gain: int) -> str:
    if gain >= HUGE_GAIN:
        return "a huge shortcut that skips most of the route"
    if gain >= GOOD_GAIN:
        return "a good shortcut"
    return "a tiny shortcut that barely helps"


def _rival_text(distance: int | None) -> str:
    if distance is None or distance <= URGENT_RIVAL:
        return "The rival is about to win the race."
    return "The rival is far behind and slow."
