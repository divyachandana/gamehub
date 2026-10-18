"""Tests for maze generation, geometry and pathfinding."""

import pytest

from inference_rally.maze import Heading, Move, generate_maze


def test_same_seed_gives_same_maze() -> None:
    assert generate_maze(7, 7, seed=3).roads == generate_maze(7, 7, seed=3).roads


def test_different_seeds_give_different_mazes() -> None:
    assert generate_maze(7, 7, seed=3).roads != generate_maze(7, 7, seed=4).roads


def test_every_junction_is_reachable() -> None:
    maze = generate_maze(7, 7, seed=11)
    for x in range(7):
        for y in range(7):
            assert maze.distance_to_exit((x, y)) >= 0


def test_shortest_path_runs_start_to_exit_on_roads() -> None:
    maze = generate_maze(6, 6, seed=5)
    path = maze.shortest_path()
    assert path[0] == maze.start and path[-1] == maze.exit
    for a, b in zip(path, path[1:], strict=False):
        assert maze.graph.has_edge(a, b)
    assert len(path) - 1 == maze.distance_to_exit(maze.start)


def test_perfect_maze_has_exactly_one_route() -> None:
    maze = generate_maze(5, 5, seed=1, loop_ratio=0.0)
    # A spanning tree over n nodes has n - 1 edges: no loops, one route.
    assert len(maze.roads) == 25 - 1


@pytest.mark.parametrize(
    ("heading", "move", "expected"),
    [
        (Heading.NORTH, Move.LEFT, Heading.WEST),
        (Heading.NORTH, Move.RIGHT, Heading.EAST),
        (Heading.EAST, Move.BACK, Heading.WEST),
        (Heading.SOUTH, Move.STRAIGHT, Heading.SOUTH),
        (Heading.WEST, Move.RIGHT, Heading.NORTH),
    ],
)
def test_relative_turns(heading: Heading, move: Move, expected: Heading) -> None:
    assert heading.turn(move) is expected


def test_options_only_include_existing_roads() -> None:
    maze = generate_maze(6, 6, seed=9)
    for node in maze.graph.nodes:
        for move, dst in maze.options(node, Heading.NORTH).items():
            assert maze.graph.has_edge(node, dst), (node, move, dst)


def test_dead_ends_have_one_road() -> None:
    maze = generate_maze(7, 7, seed=16)
    assert maze.dead_ends(), "expected at least one dead end in a branchy maze"
    for node in maze.dead_ends():
        assert maze.graph.degree[node] == 1
        assert maze.is_dead_end(node)


def test_rejects_tiny_maze() -> None:
    with pytest.raises(ValueError):
        generate_maze(1, 5, seed=0)
