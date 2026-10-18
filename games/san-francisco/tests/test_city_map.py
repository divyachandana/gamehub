"""Tests for real street maps: OpenStreetMap data to a race grid, and mazes on real streets."""

import math
from typing import Any

import pytest

from san_francisco.city_map import build_city_map, building_height, tile_range
from san_francisco.config import load_config
from san_francisco.errors import ConfigError, MapError
from san_francisco.maze import generate_maze
from san_francisco.models import RaceEvent, RaceInitEvent
from san_francisco.race import RaceEngine

LAT0, LON0 = 37.8, -122.41
AVENUES = {"Avenue A": -150.0, "Avenue B": -50.0, "Avenue C": 50.0, "Avenue D": 150.0}  # x, m
STREETS = {"First Street": -100.0, "Second Street": 0.0, "Third Street": 100.0}  # y, m
BBOX = (37.7975, -122.4132, 37.8025, -122.4068)  # about 550 m square around the grid
# A 3 x 2 grid whose middle north-south street is missing.
REAL = [
    ((0, 0), (1, 0)),
    ((1, 0), (2, 0)),
    ((0, 1), (1, 1)),
    ((1, 1), (2, 1)),
    ((0, 0), (0, 1)),
    ((2, 0), (2, 1)),
]


def _point(x: float, y: float, turn_deg: float) -> dict[str, float]:
    turn = math.radians(turn_deg)
    east = x * math.cos(turn) - y * math.sin(turn)
    north = x * math.sin(turn) + y * math.cos(turn)
    return {
        "lat": LAT0 + north / 110_574,
        "lon": LON0 + east / (111_320 * math.cos(math.radians(LAT0))),
    }


def _way(tags: dict[str, str], points: list[tuple[float, float]], turn: float) -> dict[str, Any]:
    return {"type": "way", "tags": tags, "geometry": [_point(x, y, turn) for x, y in points]}


def _grid(turn: float = 0.0) -> list[dict[str, Any]]:
    """Four avenues and three streets. Avenue B breaks between Second and Third Street (like
    a stair street); an alley and a diagonal boulevard must not become grid lines."""
    road = {"highway": "residential"}
    elements = [
        _way({**road, "name": name}, [(x, -160), (x, 160)], turn)
        for name, x in AVENUES.items()
        if name != "Avenue B"
    ]
    elements += [
        _way({**road, "name": "Avenue B"}, [(-50, -160), (-50, 10)], turn),
        _way({**road, "name": "Avenue B"}, [(-50, 90), (-50, 160)], turn),
        _way({**road, "name": "Tiny Alley"}, [(0, -100), (0, -60)], turn),
        _way({**road, "name": "Diagonal Boulevard"}, [(-150, -100), (150, 100)], turn),
    ]
    elements += [
        _way({**road, "name": name}, [(-200, y), (200, y)], turn) for name, y in STREETS.items()
    ]
    tower = {"building": "office", "height": "30", "name": "Test Tower", "wikidata": "Q1"}
    elements.append(_way(tower, [(-10, -60), (10, -60), (10, -40), (-10, -40), (-10, -60)], turn))
    park = {"leisure": "park", "name": "Test Park"}
    elements.append(_way(park, [(55, 5), (145, 5), (145, 95), (55, 95), (55, 5)], turn))
    return elements


def _block(city: Any, x: int, y: int) -> Any:
    return next(block for block in city.blocks if (block.x, block.y) == (x, y))


def test_named_straight_streets_become_the_grid() -> None:
    city = build_city_map("test", "Test", _grid(), BBOX)
    assert [line.name for line in city.columns] == list(AVENUES)
    assert [line.name for line in city.rows] == list(STREETS)
    assert (city.width, city.height, city.start, city.exit) == (4, 3, (0, 0), (3, 2))


def test_a_road_exists_only_where_the_real_street_runs() -> None:
    city = build_city_map("test", "Test", _grid(), BBOX)
    every = {((x, y), (x + 1, y)) for x in range(3) for y in range(3)}
    every |= {((x, y), (x, y + 1)) for x in range(4) for y in range(2)}
    assert set(city.streets) == every - {((1, 1), (1, 2))}


def test_turned_grid_is_found_and_straightened() -> None:
    city = build_city_map("test", "Test", _grid(turn=10.0), BBOX)
    assert city.rotation_deg == pytest.approx(10.0, abs=0.5)
    assert set(city.streets) == set(build_city_map("test", "Test", _grid(), BBOX).streets)


def test_blocks_keep_real_heights_parks_and_names() -> None:
    city = build_city_map("test", "Test", _grid(), BBOX)
    tower = _block(city, 1, 0)
    assert (tower.height_m, tower.name) == (30.0, "Test Tower")
    park = _block(city, 2, 1)
    assert (park.park, park.name) == (1.0, "Test Park")
    assert len(city.blocks) == 5 * 4  # the grid's blocks plus one ring around it


def test_area_without_a_grid_is_rejected() -> None:
    with pytest.raises(MapError):
        build_city_map("test", "Test", [], BBOX)


@pytest.mark.parametrize(
    ("tags", "meters"),
    [({"height": "12.5 m"}, 12.5), ({"building:levels": "4"}, 12.8), ({}, 9.0)],
)
def test_building_height_from_tags(tags: dict[str, str], meters: float) -> None:
    assert building_height(tags) == pytest.approx(meters)


def test_maze_on_real_streets_uses_only_them() -> None:
    maze = generate_maze(3, 2, seed=1, loop_ratio=0.0, streets=REAL)
    assert set(maze.roads) < set(REAL)
    assert len(maze.roads) == 6 - 1  # a spanning tree: the rest stay closed


def test_loop_ratio_one_opens_every_real_street() -> None:
    maze = generate_maze(3, 2, seed=1, loop_ratio=1.0, streets=REAL)
    assert set(maze.roads) == set(REAL)


def test_unreachable_exit_is_rejected() -> None:
    with pytest.raises(ValueError):
        generate_maze(3, 3, seed=1, streets=REAL)


def test_race_announces_the_city_and_its_closed_streets() -> None:
    config = load_config()
    city = config.city("sf", 0)
    assert city is not None
    maze = generate_maze(
        city.width,
        city.height,
        seed=16,
        loop_ratio=config.race.loop_ratio,
        streets=city.streets,
        start=city.start,
        exit_node=city.exit,
    )
    events: list[RaceEvent] = []
    RaceEngine(maze, [], config.race, events.append, city=city).announce()
    init = events[0]
    assert isinstance(init, RaceInitEvent)
    assert init.maze.city == city
    assert set(init.maze.closed) == set(city.streets) - set(maze.roads)
    assert init.maze.closed, "a maze on real streets closes some of them"


def test_map_tiles_cover_the_area_with_a_padding_ring() -> None:
    tiny = (37.8008, -122.4104, 37.8009, -122.4103)  # well inside one zoom-16 tile
    xs, ys = tile_range(tiny, 16)
    padded_xs, padded_ys = tile_range(tiny, 16, pad=1)
    assert (len(xs), len(ys)) == (1, 1)
    assert list(padded_xs) == [xs[0] - 1, xs[0], xs[0] + 1]
    assert list(padded_ys) == [ys[0] - 1, ys[0], ys[0] + 1]


def test_map_areas_wrap_around() -> None:
    config = load_config()
    sf = config.cities["sf"]
    assert len(sf) == 3  # North Beach, Alamo Square, Mission
    assert config.city("sf", 1) == sf[1]
    assert config.city("sf", 4) == sf[1]  # the MAP button just keeps counting
    assert config.game(None) == "sf"
    with pytest.raises(ConfigError):
        config.game("monaco")
