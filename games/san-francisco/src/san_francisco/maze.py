"""Seeded maze generation, relative-move geometry and A* pathfinding for the race track.

The maze is a grid of junctions (nodes) connected by roads (edges). Coordinates are
``(x, y)`` with ``x`` growing east and ``y`` growing north; the frontend maps them to 3D.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from enum import Enum

import networkx as nx

Node = tuple[int, int]

_HEADING_ORDER = ("N", "E", "S", "W")  # clockwise, so "right" = +1 and "left" = -1
_VECTORS: dict[str, tuple[int, int]] = {"N": (0, 1), "E": (1, 0), "S": (0, -1), "W": (-1, 0)}
_COMPASS_NAMES = {"N": "north", "E": "east", "S": "south", "W": "west"}


class Move(str, Enum):
    """A driver's choice at a junction, relative to the car's current heading."""

    LEFT = "left"
    STRAIGHT = "straight"
    RIGHT = "right"
    BACK = "back"


_TURN_OFFSETS = {Move.STRAIGHT: 0, Move.RIGHT: 1, Move.BACK: 2, Move.LEFT: 3}


class Heading(str, Enum):
    """Absolute compass direction a car is facing."""

    NORTH = "N"
    EAST = "E"
    SOUTH = "S"
    WEST = "W"

    @property
    def vector(self) -> tuple[int, int]:
        """Grid step ``(dx, dy)`` for one block in this direction."""
        return _VECTORS[self.value]

    @property
    def compass(self) -> str:
        """Human-readable name, e.g. ``"north"``."""
        return _COMPASS_NAMES[self.value]

    def turn(self, move: Move) -> Heading:
        """Return the heading after applying a relative ``move``."""
        index = _HEADING_ORDER.index(self.value)
        return Heading(_HEADING_ORDER[(index + _TURN_OFFSETS[move]) % 4])

    @staticmethod
    def between(src: Node, dst: Node) -> Heading:
        """Heading of the road going from ``src`` to the adjacent node ``dst``."""
        step = (dst[0] - src[0], dst[1] - src[1])
        for name, vector in _VECTORS.items():
            if vector == step:
                return Heading(name)
        raise ValueError(f"Nodes {src} and {dst} are not adjacent")


def _step(node: Node, heading: Heading) -> Node:
    dx, dy = heading.vector
    return (node[0] + dx, node[1] + dy)


def _manhattan(a: Node, b: Node) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class Maze:
    """An immutable road network with a start junction and an exit junction."""

    def __init__(
        self,
        width: int,
        height: int,
        roads: Iterable[tuple[Node, Node]],
        start: Node,
        exit_node: Node,
        seed: int,
    ) -> None:
        """Build the maze graph.

        Args:
            width: Number of junction columns.
            height: Number of junction rows.
            roads: Pairs of adjacent junctions that are connected by a road.
            start: Junction where every racer begins.
            exit_node: Junction racers must reach.
            seed: Seed the maze was generated from (kept for reproducibility).
        """
        self.width = width
        self.height = height
        self.start = start
        self.exit = exit_node
        self.seed = seed
        self.graph: nx.Graph = nx.Graph()
        self.graph.add_nodes_from((x, y) for x in range(width) for y in range(height))
        self.graph.add_edges_from(roads)
        # The maze never changes, so precompute lookups the race engine hits every step.
        self._distance_to_exit: dict[Node, int] = dict(
            nx.single_source_shortest_path_length(self.graph, exit_node)
        )
        self._dead_ends = frozenset(
            node
            for node, degree in self.graph.degree
            if degree == 1 and node not in (self.start, self.exit)
        )

    @property
    def roads(self) -> list[tuple[Node, Node]]:
        """All roads as sorted node pairs (stable order for serialization)."""
        return sorted(tuple(sorted(edge)) for edge in self.graph.edges)  # type: ignore[misc]

    def options(self, node: Node, heading: Heading) -> dict[Move, Node]:
        """Return every legal move from ``node`` and the junction it leads to."""
        result: dict[Move, Node] = {}
        for move in Move:
            neighbor = _step(node, heading.turn(move))
            if self.graph.has_edge(node, neighbor):
                result[move] = neighbor
        return result

    def hedge_neighbors(self, node: Node) -> list[Node]:
        """Adjacent junctions with a hedge (no road) in between: where a rogue can jump."""
        x, y = node
        return [
            (x + dx, y + dy)
            for dx, dy in _VECTORS.values()
            if 0 <= x + dx < self.width
            and 0 <= y + dy < self.height
            and not self.graph.has_edge(node, (x + dx, y + dy))
        ]

    def distance_to_exit(self, node: Node) -> int:
        """Number of road segments on the shortest route from ``node`` to the exit."""
        return self._distance_to_exit[node]

    def shortest_path(self, src: Node | None = None) -> list[Node]:
        """A* route from ``src`` (default: start) to the exit, inclusive of both ends."""
        origin = self.start if src is None else src
        return list(nx.astar_path(self.graph, origin, self.exit, heuristic=_manhattan))

    def is_optimal_step(self, src: Node, dst: Node) -> bool:
        """True if driving ``src -> dst`` keeps the car on a shortest route to the exit."""
        return self.distance_to_exit(dst) == self.distance_to_exit(src) - 1

    def dead_ends(self) -> list[Node]:
        """Junctions with a single road, excluding the start and the exit."""
        return sorted(self._dead_ends)

    def is_dead_end(self, node: Node) -> bool:
        """True if ``node`` is a dead end (only one road in or out)."""
        return node in self._dead_ends

    def is_junction(self, node: Node, heading: Heading) -> bool:
        """True if a driver arriving with ``heading`` has a real choice (2+ roads ahead)."""
        forward = [move for move in self.options(node, heading) if move is not Move.BACK]
        return len(forward) >= 2

    def initial_heading(self) -> Heading:
        """Face the car down the first road of the optimal route so the race starts cleanly."""
        return Heading.between(self.start, self.shortest_path()[1])


def generate_maze(
    width: int,
    height: int,
    seed: int,
    loop_ratio: float = 0.08,
    branchiness: float = 0.5,
    streets: Iterable[tuple[Node, Node]] | None = None,
    start: Node = (0, 0),
    exit_node: Node | None = None,
) -> Maze:
    """Generate a reproducible maze with junctions, dead ends and a few loops.

    Uses the "growing tree" algorithm: each step extends either the newest cell
    (long winding corridors, like depth-first search) or a random open cell (many
    short branches, like Prim's). ``branchiness`` blends the two, so the maze has
    real choices at junctions instead of one long corridor. Afterwards a fraction of
    the remaining walls is removed so wrong turns are recoverable.

    Args:
        width: Junction columns (>= 2).
        height: Junction rows (>= 2).
        seed: RNG seed; the same seed always produces the same maze.
        loop_ratio: Fraction of remaining walls to remove (0 = exactly one route).
        branchiness: 0 = corridors only, 1 = maximally branchy.
        streets: Real roads to carve the maze from (default: every road of the grid).
            Streets the maze does not use stay closed, so a real street map is still a
            maze; ``loop_ratio=1`` opens every real street.
        start: Junction where every racer begins.
        exit_node: Junction racers must reach (default: the north-east corner).

    Returns:
        The generated maze.

    Raises:
        ValueError: If the grid is too small, a ratio is out of range or the exit
            cannot be reached from the start on ``streets``.
    """
    if width < 2 or height < 2:
        raise ValueError("Maze must be at least 2x2")
    if not 0.0 <= loop_ratio <= 1.0 or not 0.0 <= branchiness <= 1.0:
        raise ValueError("loop_ratio and branchiness must be between 0 and 1")

    allowed = None if streets is None else {_edge(a, b) for a, b in streets}
    exit_node = exit_node or (width - 1, height - 1)
    rng = random.Random(seed)
    roads: set[tuple[Node, Node]] = set()
    visited = {start}
    frontier = [start]

    while frontier:
        index = rng.randrange(len(frontier)) if rng.random() < branchiness else len(frontier) - 1
        node = frontier[index]
        unvisited = [
            neighbor
            for neighbor in (_step(node, heading) for heading in Heading)
            if 0 <= neighbor[0] < width
            and 0 <= neighbor[1] < height
            and neighbor not in visited
            and (allowed is None or _edge(node, neighbor) in allowed)
        ]
        if not unvisited:
            frontier.pop(index)
            continue
        nxt = rng.choice(unvisited)
        roads.add(_edge(node, nxt))
        visited.add(nxt)
        frontier.append(nxt)

    if exit_node not in visited:
        raise ValueError(f"The exit {exit_node} cannot be reached from the start {start}")
    grid = (
        _edge((x, y), neighbor)
        for x in range(width)
        for y in range(height)
        for neighbor in ((x + 1, y), (x, y + 1))
        if neighbor[0] < width and neighbor[1] < height
    )
    walls = sorted(
        edge
        for edge in grid
        if edge not in roads
        and (allowed is None or edge in allowed)
        and edge[0] in visited  # a street cut off from the start stays out of the race
        and edge[1] in visited
    )
    roads.update(rng.sample(walls, k=round(len(walls) * loop_ratio)))

    return Maze(width, height, roads, start=start, exit_node=exit_node, seed=seed)


def _edge(a: Node, b: Node) -> tuple[Node, Node]:
    """Canonical (sorted) representation of an undirected road."""
    return (a, b) if a <= b else (b, a)
