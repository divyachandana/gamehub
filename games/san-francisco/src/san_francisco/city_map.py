"""Real street maps: turn OpenStreetMap data into a race grid of real streets.

The area is turned so its street grid runs along the axes; long, straight, named streets
become the grid's columns (north-south) and rows (east-west), and a road joins two
neighboring junctions where the real street runs between them. Each block keeps the
typical height of its real buildings and its share of park, so the frontend can build a
city that looks like the real one. Downloading lives in ``scripts/fetch_map.py``.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import networkx as nx

from san_francisco.errors import MapError
from san_francisco.maze import Node
from san_francisco.models import CityBlock, CityMap, SkylineBuilding, StreetLine

ATTRIBUTION = "Map data © OpenStreetMap contributors (ODbL)"
METERS_PER_DEGREE_LAT = 110_574.0
METERS_PER_DEGREE_LON = 111_320.0
AXIS_TOLERANCE_DEG = 20.0  # segments farther than this from both axes (diagonals) are skipped
MIN_STREET_SHARE = 0.35  # streets shorter than this share of the area (alleys) are not grid lines
MIN_STREET_GAP_M = 30.0  # lines closer than this are one street (divided roads, small jogs)
SAME_NAME_GAP_M = 60.0  # same-name segments farther apart than this are different streets
LINE_SNAP_M = 25.0  # a segment belongs to its line when this close to it
ROAD_COVERAGE = 0.85  # share of a block edge a street must cover to be a road there
PARK_SAMPLES = 5  # park share is sampled on a 5 x 5 grid per block
PARK_BLOCK_SHARE = 0.5  # a block at least this much park is named after the park
METERS_PER_LEVEL = 3.2
DEFAULT_HEIGHT_M = 9.0  # untagged buildings: a typical three-floor row house
SKYLINE_MIN_HEIGHT_M = 60.0
MIN_BUILDING_M2 = 4.0  # smaller outlines (sheds, kiosks) are not worth drawing
FEATURE_MARGIN_DEG = 0.002  # about 200 m of streets drawn past the area
MAX_SKYLINE = 150

Point = tuple[float, float]
Element = dict[str, Any]  # one element of an Overpass API JSON answer
Bbox = tuple[float, float, float, float]  # south, west, north, east


@dataclass(frozen=True)
class Frame:
    """Flat local frame in meters around an origin, turned so the streets run along the axes."""

    lat0: float
    lon0: float
    rotation: float = 0.0  # radians, street grid angle counterclockwise from east

    def project(self, lat: float, lon: float) -> Point:
        """Meters along the grid's east-west and north-south axes from the origin."""
        east = (lon - self.lon0) * METERS_PER_DEGREE_LON * math.cos(math.radians(self.lat0))
        north = (lat - self.lat0) * METERS_PER_DEGREE_LAT
        cos, sin = math.cos(self.rotation), math.sin(self.rotation)
        return east * cos + north * sin, north * cos - east * sin


@dataclass(frozen=True)
class _Segment:
    offset: float  # position across the street
    start: float  # extent along the street
    end: float


@dataclass(frozen=True)
class _Line:
    name: str
    offset: float
    segments: tuple[_Segment, ...]

    @property
    def length(self) -> float:
        return sum(s.end - s.start for s in self.segments)


@dataclass(frozen=True)
class _Building:
    centre: Point
    area: float
    height: float
    name: str | None  # only for notable buildings (they have a Wikidata entry)


def build_city_map(
    name: str,
    title: str,
    elements: Sequence[Element],
    bbox: Bbox,
    size: int = 7,
    tall: Sequence[Element] = (),
) -> CityMap:
    """Make a race map from OpenStreetMap elements.

    Args:
        name: Short map id, e.g. ``"sf-north-beach"``.
        title: Name shown in the game, e.g. ``"San Francisco · North Beach"``.
        elements: Overpass ``out geom`` elements: named roads, buildings and parks.
        bbox: The area (south, west, north, east) the elements were fetched for.
        size: Most junction columns and rows to keep (the central ones).
        tall: Overpass elements for tall buildings around the area (the skyline).

    Returns:
        The street map.

    Raises:
        MapError: If the area has no usable street grid.

    """
    south, west, north, east = bbox
    flat = Frame((south + north) / 2, (west + east) / 2)
    roads = [
        (way["tags"]["name"], _inside_bbox(way["geometry"], bbox))
        for way in elements
        if way.get("type") == "way" and "highway" in way.get("tags", {}) and "name" in way["tags"]
    ]
    rotation = grid_rotation(
        [flat.project(p["lat"], p["lon"]) for p in part] for _, parts in roads for part in parts
    )
    frame = Frame(flat.lat0, flat.lon0, rotation)

    by_name: dict[str, tuple[list[_Segment], list[_Segment]]] = defaultdict(lambda: ([], []))
    for street, parts in roads:
        for part in parts:
            points = [frame.project(p["lat"], p["lon"]) for p in part]
            north_south, east_west = _split_segments(points)
            by_name[street][0].extend(north_south)
            by_name[street][1].extend(east_west)
    span_x, span_y = _size_m(bbox)
    columns = _central(_lines({k: v[0] for k, v in by_name.items()}, span_y), size)
    rows = _central(_lines({k: v[1] for k, v in by_name.items()}, span_x), size)
    if len(columns) < 2 or len(rows) < 2:
        raise MapError(
            f"Found {len(columns)} north-south and {len(rows)} east-west streets here; "
            "pick an area with a street grid"
        )
    streets, start, exit_node = _connected(_grid_roads(columns, rows))
    xs, ys = _ring_edges(columns), _ring_edges(rows)

    return CityMap(
        name=name,
        title=title,
        attribution=ATTRIBUTION,
        origin=(round(frame.lat0, 6), round(frame.lon0, 6)),
        rotation_deg=round(math.degrees(rotation), 3),
        columns=[StreetLine(name=line.name, offset_m=round(line.offset, 1)) for line in columns],
        rows=[StreetLine(name=line.name, offset_m=round(line.offset, 1)) for line in rows],
        streets=streets,
        start=start,
        exit=exit_node,
        blocks=_blocks(_buildings(elements, frame), _parks(elements, frame), xs, ys),
        skyline=_skyline(tall, frame, xs, ys),
        bbox=bbox,
    )


def grid_rotation(lines: Iterable[Sequence[Point]]) -> float:
    """Dominant street direction in radians, folded into (-45°, 45°].

    A grid looks the same every 90°, so angles are averaged on a circle of four turns.
    Diagonal avenues pull that average off, so it is taken again over the segments that
    run near the first estimate's axes.
    """
    segments = [
        (math.hypot(x2 - x1, y2 - y1), math.atan2(y2 - y1, x2 - x1))
        for line in lines
        for (x1, y1), (x2, y2) in zip(line, line[1:], strict=False)
    ]
    estimate = _mean_axis(segments)
    tolerance = math.radians(AXIS_TOLERANCE_DEG)
    aligned = [(w, a) for w, a in segments if _axis_gap(a, estimate) <= tolerance]
    return _mean_axis(aligned or segments)


def _mean_axis(segments: Sequence[tuple[float, float]]) -> float:
    sx = sum(length * math.cos(4 * angle) for length, angle in segments)
    sy = sum(length * math.sin(4 * angle) for length, angle in segments)
    return math.atan2(sy, sx) / 4


def _axis_gap(angle: float, axis: float) -> float:
    """Angle between a direction and the nearest of the grid's four axis directions."""
    gap = (angle - axis) % (math.pi / 2)
    return min(gap, math.pi / 2 - gap)


def map_features(elements: Sequence[Element], city: CityMap) -> dict[str, list[list[Any]]]:
    """Describe the area for the frontend's drawn map, in the map's turned frame (meters).

    Returns:
        ``buildings``: ``[height_m, x0, y0, ...]``; ``roads``: ``[kind, name, x0, y0, ...]``;
        ``parks``: ``[x0, y0, ...]``.

    """
    frame = Frame(city.origin[0], city.origin[1], math.radians(city.rotation_deg))

    def flat(points: list[Point]) -> list[float]:
        return [round(value, 1) for point in points for value in point]

    def outline(geometry: list[dict[str, float]]) -> list[Point]:
        polygon = [frame.project(p["lat"], p["lon"]) for p in geometry]
        return polygon[:-1] if polygon and polygon[0] == polygon[-1] else polygon

    # Streets come back whole from Overpass; keep the part near the area, not the whole city.
    south, west, north, east = city.bbox or (-90.0, -180.0, 90.0, 180.0)
    near: Bbox = (
        south - FEATURE_MARGIN_DEG,
        west - FEATURE_MARGIN_DEG,
        north + FEATURE_MARGIN_DEG,
        east + FEATURE_MARGIN_DEG,
    )
    features: dict[str, list[list[Any]]] = {"buildings": [], "roads": [], "parks": []}
    for element in elements:
        tags = element.get("tags", {})
        if element.get("type") == "way" and "building" in tags:
            polygon = outline(element["geometry"])
            if len(polygon) >= 3 and _area(polygon) >= MIN_BUILDING_M2:
                features["buildings"].append([round(building_height(tags), 1), *flat(polygon)])
        elif element.get("type") == "way" and "highway" in tags:
            for part in _inside_bbox(element["geometry"], near):
                line = [frame.project(p["lat"], p["lon"]) for p in part]
                features["roads"].append([tags["highway"], tags.get("name", ""), *flat(line)])
        elif tags.get("leisure") == "park":
            outlines = (
                [element["geometry"]]
                if element.get("type") == "way"
                else [m["geometry"] for m in element.get("members", []) if m.get("role") == "outer"]
            )
            for geometry in outlines:
                polygon = outline(geometry)
                if len(polygon) >= 3:
                    features["parks"].append(flat(polygon))
    return features


def tile_range(bbox: Bbox, zoom: int, pad: int = 0) -> tuple[range, range]:
    """Map tiles (x, y) covering ``bbox`` at ``zoom``, plus ``pad`` tiles around it."""
    south, west, north, east = bbox
    x0, y0 = _tile(north, west, zoom)
    x1, y1 = _tile(south, east, zoom)
    return range(x0 - pad, x1 + pad + 1), range(y0 - pad, y1 + pad + 1)


def _tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    count = 2**zoom
    x = (lon + 180) / 360 * count
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * count
    return int(x), int(y)


def building_height(tags: dict[str, str]) -> float:
    """Height in meters from OSM tags: ``height``, else ``building:levels``, else a default."""
    height = _number(tags.get("height", ""))
    if height:
        return height
    levels = _number(tags.get("building:levels", ""))
    return levels * METERS_PER_LEVEL if levels else DEFAULT_HEIGHT_M


# --------------------------------------------------------------------------- streets


def _inside_bbox(geometry: list[dict[str, float]], bbox: Bbox) -> list[list[dict[str, float]]]:
    """Pieces of a way inside the area (Overpass returns whole ways that cross it)."""
    south, west, north, east = bbox
    parts: list[list[dict[str, float]]] = [[]]
    for point in geometry:
        if south <= point["lat"] <= north and west <= point["lon"] <= east:
            parts[-1].append(point)
        elif parts[-1]:
            parts.append([])
    return [part for part in parts if len(part) >= 2]


def _split_segments(points: Sequence[Point]) -> tuple[list[_Segment], list[_Segment]]:
    """Split a street into north-south (column) and east-west (row) segments."""
    columns: list[_Segment] = []
    rows: list[_Segment] = []
    for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
        angle = math.degrees(math.atan2(abs(y2 - y1), abs(x2 - x1)))
        if angle <= AXIS_TOLERANCE_DEG:
            rows.append(_Segment((y1 + y2) / 2, min(x1, x2), max(x1, x2)))
        elif angle >= 90 - AXIS_TOLERANCE_DEG:
            columns.append(_Segment((x1 + x2) / 2, min(y1, y2), max(y1, y2)))
    return columns, rows


def _lines(by_name: dict[str, list[_Segment]], span: float) -> list[_Line]:
    """Long straight streets sorted across the area; alleys and duplicates dropped."""
    lines = [
        _Line(name, _weighted_median(cluster), tuple(cluster))
        for name, segments in by_name.items()
        for cluster in _clusters(segments)
    ]
    kept: list[_Line] = []
    for line in sorted((li for li in lines if li.length >= MIN_STREET_SHARE * span), key=_offset):
        if kept and line.offset - kept[-1].offset < MIN_STREET_GAP_M:
            if line.length > kept[-1].length:
                kept[-1] = line
            continue
        kept.append(line)
    return kept


def _offset(line: _Line) -> float:
    return line.offset


def _clusters(segments: list[_Segment]) -> list[list[_Segment]]:
    """Group a name's segments into streets: one name can label two separate streets."""
    clusters: list[list[_Segment]] = []
    for segment in sorted(segments, key=lambda s: s.offset):
        if clusters and segment.offset - clusters[-1][-1].offset <= SAME_NAME_GAP_M:
            clusters[-1].append(segment)
        else:
            clusters.append([segment])
    return clusters


def _weighted_median(segments: list[_Segment]) -> float:
    ordered = sorted(segments, key=lambda s: s.offset)
    half = sum(s.end - s.start for s in ordered) / 2
    total = 0.0
    for segment in ordered:
        total += segment.end - segment.start
        if total >= half:
            return segment.offset
    return ordered[-1].offset


def _central(lines: list[_Line], size: int) -> list[_Line]:
    """Keep the ``size`` neighboring lines closest to the middle of the area."""
    if len(lines) <= size:
        return lines
    first = min(
        range(len(lines) - size + 1),
        key=lambda k: abs(lines[k].offset + lines[k + size - 1].offset),
    )
    return lines[first : first + size]


def _covers(line: _Line, low: float, high: float) -> bool:
    """Tell whether the street runs (almost) all the way from ``low`` to ``high``."""
    spans = sorted(
        (max(s.start, low), min(s.end, high))
        for s in line.segments
        if abs(s.offset - line.offset) <= LINE_SNAP_M
    )
    covered, reach = 0.0, low
    for start, end in spans:
        if end > reach:
            covered += end - max(start, reach)
            reach = end
    return covered >= ROAD_COVERAGE * (high - low)


def _grid_roads(columns: list[_Line], rows: list[_Line]) -> list[tuple[Node, Node]]:
    roads: list[tuple[Node, Node]] = []
    for y, row in enumerate(rows):
        for x in range(len(columns) - 1):
            if _covers(row, columns[x].offset, columns[x + 1].offset):
                roads.append(((x, y), (x + 1, y)))
    for x, column in enumerate(columns):
        for y in range(len(rows) - 1):
            if _covers(column, rows[y].offset, rows[y + 1].offset):
                roads.append(((x, y), (x, y + 1)))
    return roads


def _connected(roads: list[tuple[Node, Node]]) -> tuple[list[tuple[Node, Node]], Node, Node]:
    """Roads of the largest connected part, with its south-west and north-east corners."""
    graph: nx.Graph = nx.Graph(roads)
    if graph.number_of_nodes() == 0:
        raise MapError("No street runs between two junctions here; pick an area with a grid")
    nodes: set[Node] = max(nx.connected_components(graph), key=len)
    start = min(nodes, key=lambda n: (n[0] + n[1], n[0]))
    exit_node = max(nodes, key=lambda n: (n[0] + n[1], n[0]))
    return sorted(edge for edge in roads if edge[0] in nodes), start, exit_node


def _ring_edges(lines: list[_Line]) -> list[float]:
    """Line offsets plus one guessed line on each side, for the ring of blocks around."""
    offsets = [line.offset for line in lines]
    return [2 * offsets[0] - offsets[1], *offsets, 2 * offsets[-1] - offsets[-2]]


# --------------------------------------------------------------------------- blocks


def _buildings(elements: Sequence[Element], frame: Frame) -> list[_Building]:
    buildings = []
    for way in elements:
        tags = way.get("tags", {})
        if way.get("type") != "way" or "building" not in tags:
            continue
        polygon = [frame.project(p["lat"], p["lon"]) for p in way["geometry"]]
        area = _area(polygon)
        if area < 1:
            continue
        notable = "wikidata" in tags or "wikipedia" in tags
        name = tags.get("name") if notable else None
        buildings.append(_Building(_centre(polygon), area, building_height(tags), name))
    return buildings


def _parks(elements: Sequence[Element], frame: Frame) -> list[tuple[str | None, list[Point]]]:
    parks = []
    for element in elements:
        if element.get("tags", {}).get("leisure") != "park":
            continue
        if element.get("type") == "way":
            outlines = [element["geometry"]]
        else:
            members = element.get("members", [])
            outlines = [m["geometry"] for m in members if m.get("role") == "outer"]
        for outline in outlines:
            polygon = [frame.project(p["lat"], p["lon"]) for p in outline]
            parks.append((element["tags"].get("name"), polygon))
    return parks


def _blocks(
    buildings: list[_Building],
    parks: list[tuple[str | None, list[Point]]],
    xs: list[float],
    ys: list[float],
) -> list[CityBlock]:
    """Stats for every block of the grid and of the ring around it."""
    on_block: dict[tuple[int, int], list[_Building]] = defaultdict(list)
    for building in buildings:
        x = bisect_right(xs, building.centre[0]) - 2
        y = bisect_right(ys, building.centre[1]) - 2
        if -1 <= x < len(xs) - 2 and -1 <= y < len(ys) - 2:
            on_block[(x, y)].append(building)

    blocks = []
    for x in range(-1, len(xs) - 2):
        for y in range(-1, len(ys) - 2):
            x0, x1, y0, y1 = xs[x + 1], xs[x + 2], ys[y + 1], ys[y + 2]
            own = on_block[(x, y)]
            built = sum(b.area for b in own)
            height = sum(b.area * b.height for b in own) / built if built else 0.0
            tallest = max(own, key=lambda b: b.height, default=None)
            park, park_name = _park_share(parks, x0, x1, y0, y1)
            named = [b for b in own if b.name]
            name = park_name if park >= PARK_BLOCK_SHARE else None
            if name is None and named:
                name = max(named, key=lambda b: b.area).name
            blocks.append(
                CityBlock(
                    x=x,
                    y=y,
                    height_m=round(height, 1),
                    max_height_m=round(tallest.height, 1) if tallest else 0.0,
                    built=round(min(1.0, built / ((x1 - x0) * (y1 - y0))), 2),
                    park=round(park, 2),
                    name=name,
                )
            )
    return blocks


def _park_share(
    parks: list[tuple[str | None, list[Point]]], x0: float, x1: float, y0: float, y1: float
) -> tuple[float, str | None]:
    hits: Counter[int] = Counter()
    for i in range(PARK_SAMPLES):
        for j in range(PARK_SAMPLES):
            point = (
                x0 + (i + 0.5) / PARK_SAMPLES * (x1 - x0),
                y0 + (j + 0.5) / PARK_SAMPLES * (y1 - y0),
            )
            for index, (_, polygon) in enumerate(parks):
                if _contains(polygon, point):
                    hits[index] += 1
                    break
    if not hits:
        return 0.0, None
    return sum(hits.values()) / PARK_SAMPLES**2, parks[hits.most_common(1)[0][0]][0]


def _skyline(
    tall: Sequence[Element], frame: Frame, xs: list[float], ys: list[float]
) -> list[SkylineBuilding]:
    """Tall buildings outside the blocks, tallest first."""
    found: dict[tuple[float, float], SkylineBuilding] = {}
    for way in tall:
        tags = way.get("tags", {})
        height = building_height(tags)
        if way.get("type") != "way" or height < SKYLINE_MIN_HEIGHT_M:
            continue
        if "bridge:support" in tags or "man_made" in tags:
            continue  # bridge towers, smokestacks: not part of the skyline
        geometry = way["geometry"]
        polygon = [frame.project(p["lat"], p["lon"]) for p in geometry]
        x, y = _centre(polygon)
        if xs[0] <= x <= xs[-1] and ys[0] <= y <= ys[-1]:
            continue  # inside the city: its block's height already shows it
        lat = sum(p["lat"] for p in geometry) / len(geometry)
        lon = sum(p["lon"] for p in geometry) / len(geometry)
        found[(round(lat, 4), round(lon, 4))] = SkylineBuilding(
            name=tags.get("name"),
            lat=round(lat, 6),
            lon=round(lon, 6),
            height_m=round(height, 1),
            width_m=round(max(8.0, max(p[0] for p in polygon) - min(p[0] for p in polygon)), 1),
            depth_m=round(max(8.0, max(p[1] for p in polygon) - min(p[1] for p in polygon)), 1),
        )
    return sorted(found.values(), key=lambda b: -b.height_m)[:MAX_SKYLINE]


# --------------------------------------------------------------------------- geometry


def _size_m(bbox: Bbox) -> Point:
    south, west, north, east = bbox
    lat0 = math.radians((south + north) / 2)
    return (
        (east - west) * METERS_PER_DEGREE_LON * math.cos(lat0),
        (north - south) * METERS_PER_DEGREE_LAT,
    )


def _area(polygon: list[Point]) -> float:
    pairs = zip(polygon, polygon[1:] + polygon[:1], strict=False)
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in pairs)) / 2


def _centre(polygon: list[Point]) -> Point:
    return (
        sum(p[0] for p in polygon) / len(polygon),
        sum(p[1] for p in polygon) / len(polygon),
    )


def _contains(polygon: list[Point], point: Point) -> bool:
    """Ray casting: inside if a ray from the point crosses the outline an odd number of times."""
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1], strict=False):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _number(text: str) -> float | None:
    """Leading number of a tag such as ``"12"``, ``"12.5 m"`` or ``"4;5"``."""
    digits = ""
    for char in text.strip():
        if char.isdigit() or (char == "." and "." not in digits):
            digits += char
        else:
            break
    return float(digits) if digits.strip(".") else None
