"""End-to-end tests for the race engine with fast simulated racers."""

import asyncio

from san_francisco.config import build_decider, load_config
from san_francisco.maze import generate_maze
from san_francisco.models import RaceEvent, RacerSpec, SimProfile
from san_francisco.race import RaceEngine

FAST = SimProfile(tps=5000, ttft_ms=0, accuracy=0.6, jitter=0)


def _run(seed: int = 16) -> tuple[list[RaceEvent], dict[str, object]]:
    config = load_config()
    settings = config.race.model_copy(
        update={"segment_time_s": 0.0, "uturn_penalty_s": 0.0, "countdown_s": 0, "seed": seed}
    )
    maze = generate_maze(
        settings.maze_width, settings.maze_height, seed, settings.loop_ratio, settings.branchiness
    )
    specs: list[RacerSpec] = [s.model_copy(update={"sim": FAST}) for s in config.racers("sim")]
    events: list[RaceEvent] = []

    def emit(event: RaceEvent) -> None:
        events.append(event)

    engine = RaceEngine(maze, [(s, build_decider(s, maze, seed)) for s in specs], settings, emit)
    results = asyncio.run(engine.run())
    return events, results  # type: ignore[return-value]


def test_every_racer_finishes_with_a_unique_place() -> None:
    events, results = _run()
    finishes = [e for e in events if e.type == "finish"]
    assert sorted(e.place for e in finishes) == [1, 2]
    assert set(results) == {"gemma", "qwen"}


def test_event_stream_is_well_ordered() -> None:
    events, _ = _run()
    assert events[0].type == "race_init"
    assert events[1].type == "race_start"
    assert events[-1].type == "race_end"
    times = [e.t for e in events]
    assert times == sorted(times)


def test_every_decision_is_preceded_by_thinking() -> None:
    events, _ = _run()
    thinking: dict[str, int] = {}
    for event in events:
        if event.type == "thinking":
            thinking[event.racer] = thinking.get(event.racer, 0) + 1
        elif event.type == "decision":
            thinking[event.racer] -= 1
            assert thinking[event.racer] == 0


def test_drives_follow_roads_and_end_at_exit() -> None:
    events, _ = _run()
    init = events[0]
    assert init.type == "race_init"
    roads = {tuple(sorted(road)) for road in init.maze.roads}
    last_node: dict[str, tuple[int, int]] = {}
    for event in events:
        if event.type == "drive":
            assert tuple(sorted((event.src, event.dst))) in roads
            last_node[event.racer] = event.dst
    assert all(node == init.maze.exit for node in last_node.values())
