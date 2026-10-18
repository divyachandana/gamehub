"""Tests for UNFAIR mode: cheat planning and a rogue racing with a fake Laya judge."""

import asyncio
from collections import Counter

from inference_rally.config import build_decider, load_config
from inference_rally.maze import Heading, Maze, generate_maze
from inference_rally.models import RaceEvent, RogueSpec, SimProfile
from inference_rally.race import RaceEngine
from inference_rally.rogue import DRONE_QUESTION, JUMP_QUESTION, plan_cheat

FAST = SimProfile(tps=5000, ttft_ms=0, accuracy=0.6, jitter=0)
ROGUE = RogueSpec(jump_penalty_s=0, drone_time_s=0)

# A U-shaped 3x2 maze: (0,0)-(0,1)-(1,1)-(2,1)-(2,0)-(1,0). From the start (0,0) the exit
# (1,0) is 5 roads away, but only one hedge away.
U_ROADS = [((0, 0), (0, 1)), ((0, 1), (1, 1)), ((1, 1), (2, 1)), ((2, 1), (2, 0)), ((1, 0), (2, 0))]


def _u_maze() -> Maze:
    return Maze(3, 2, U_ROADS, start=(0, 0), exit_node=(1, 0), seed=0)


def test_hedge_neighbors_are_adjacent_junctions_without_a_road() -> None:
    maze = _u_maze()
    assert maze.hedge_neighbors((0, 0)) == [(1, 0)]
    assert sorted(maze.hedge_neighbors((1, 1))) == [(1, 0)]


def test_plan_offers_a_jump_that_saves_enough_blocks() -> None:
    maze = _u_maze()
    plan = plan_cheat(maze, (0, 0), Heading.NORTH, Counter(), ROGUE, 3, 2, 10)
    assert plan is not None and plan.kind == "jump" and plan.target == (1, 0)
    assert plan.gain == 5 and plan.question == JUMP_QUESTION
    assert "good shortcut" in plan.state and "far behind" in plan.state


def test_plan_skips_jumps_without_charges_or_gain() -> None:
    maze = _u_maze()
    assert plan_cheat(maze, (0, 0), Heading.NORTH, Counter(), ROGUE, 0, 0, 10) is None
    small = ROGUE.model_copy(update={"min_jump_gain": 6})
    assert plan_cheat(maze, (0, 0), Heading.NORTH, Counter(), small, 3, 0, 10) is None


def test_plan_offers_a_drone_when_circling() -> None:
    maze = generate_maze(7, 7, 16)
    node, heading = next(
        (n, h) for n in maze.graph.nodes for h in Heading if maze.is_junction(n, h)
    )
    visits = Counter({dst: 3 for dst in maze.options(node, heading).values()})
    no_jumps = ROGUE.model_copy(update={"jumps": 0})
    plan = plan_cheat(maze, node, heading, visits, no_jumps, 0, 1, 2)
    assert plan is not None and plan.kind == "drone" and plan.question == DRONE_QUESTION
    assert "completely lost" in plan.state and "about to win" in plan.state


def _race(probability: float | None) -> tuple[list[RaceEvent], dict[str, object]]:
    config = load_config()
    settings = config.race.model_copy(
        update={"segment_time_s": 0.0, "uturn_penalty_s": 0.0, "countdown_s": 0, "seed": 16}
    )
    maze = generate_maze(7, 7, 16, settings.loop_ratio, settings.branchiness)
    honest, rogue = (s.model_copy(update={"sim": FAST}) for s in config.racers("sim"))
    rogue = rogue.model_copy(update={"id": "rogue", "rogue": ROGUE})
    questions: list[str] = []

    async def judge(state: str, question: str) -> float:
        questions.append(question)
        assert probability is not None
        return probability

    racers = [(s, build_decider(s, maze, 16)) for s in (honest, rogue)]
    events: list[RaceEvent] = []
    engine = RaceEngine(
        maze, racers, settings, events.append, None if probability is None else judge
    )
    results = asyncio.run(engine.run())
    return events, results  # type: ignore[return-value]


def test_rogue_cheats_when_laya_says_yes() -> None:
    events, results = _race(probability=0.9)
    cheats = [e for e in events if e.type == "cheat"]
    assert cheats and all(c.used for c in cheats)
    jumps = [e for e in events if e.type == "drive" and e.jump]
    assert len(jumps) == sum(c.kind == "jump" for c in cheats) <= ROGUE.jumps
    for jump in jumps:  # a jump lands on an adjacent junction, across a hedge
        assert abs(jump.src[0] - jump.dst[0]) + abs(jump.src[1] - jump.dst[1]) == 1
    assert results["rogue"].cheats == len(cheats)  # type: ignore[attr-defined]
    init = events[0]
    assert init.type == "race_init" and [r.rogue for r in init.racers] == [False, True]


def test_rogue_stays_honest_when_laya_says_no_or_is_missing() -> None:
    for probability in (0.1, None):
        events, results = _race(probability)
        assert not any(e.type == "drive" and e.jump for e in events)
        assert results["rogue"].cheats == 0  # type: ignore[attr-defined]
        declined = [e for e in events if e.type == "cheat"]
        assert all(not c.used for c in declined)
        assert bool(declined) == (probability is not None)


def test_drone_has_its_own_lower_threshold() -> None:
    events, _ = _race(probability=0.2)  # below the jump bar (0.4), above the drone bar (0.15)
    cheats = [e for e in events if e.type == "cheat"]
    assert cheats
    for cheat in cheats:
        assert cheat.used == (cheat.kind == "drone")
