"""Download a street map from OpenStreetMap and save it as a race map.

Usage:
    python scripts/fetch_map.py sf-north-beach        # an area named in maps/areas.yaml
    python scripts/fetch_map.py my-area --bbox 37.79,-122.42,37.80,-122.40 --title "My area"

Writes ``maps/<name>.json``; add that file to a game's ``maps`` in racers.yaml. The raw
download is kept in ``maps/.cache/`` so a re-run only re-processes (``--refresh`` downloads
again). The area's street map pictures (OpenStreetMap's own map tiles, for the game's map
panel) go to ``maps/tiles/`` once each. Map data © OpenStreetMap contributors, under the
Open Database License.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from san_francisco.city_map import (
    SKYLINE_MIN_HEIGHT_M,
    Bbox,
    build_city_map,
    map_features,
    tile_range,
)
from san_francisco.errors import MapError

ROOT = Path(__file__).resolve().parents[1]
MAPS = ROOT / "maps"
ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
SKYLINE_RADIUS_M = 7000  # tall buildings this far away still show on the horizon
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_ZOOMS = (16, 17)  # 16 fits the map panel; 17 is sharp when the map is full screen
TILE_PAUSE_S = 0.2  # OpenStreetMap's tile servers ask for few, unhurried requests
TILE_PAD = 1  # one extra ring, so a tall or wide map panel never shows a blank edge
USER_AGENT = "inference-rally-map-fetch/0.1 (+https://github.com/divyachandana/Inference-Rally)"
DRIVABLE = "primary|secondary|tertiary|unclassified|residential|living_street|trunk"

AREA_QUERY = """
[out:json][timeout:180];
(
  way["highway"~"^({drivable})$"]["name"]({bbox});
  way["building"]({bbox});
  way["leisure"="park"]({bbox});
  relation["leisure"="park"]({bbox});
);
out geom;
"""
SKYLINE_QUERY = """
[out:json][timeout:180];
way["building"]["height"](around:{radius},{lat},{lon})(if: number(t["height"]) >= {min_height});
out geom;
"""


def main() -> None:
    """Fetch one area and write its race map."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="map name: an area in maps/areas.yaml, or new with --bbox")
    parser.add_argument("--bbox", help="south,west,north,east in degrees")
    parser.add_argument("--title", help="name shown in the game")
    parser.add_argument("--size", type=int, default=7, help="most junctions per side (default 7)")
    parser.add_argument("--refresh", action="store_true", help="download again, ignore the cache")
    args = parser.parse_args()

    title, bbox = _area(args.name, args.bbox, args.title)
    area_query = AREA_QUERY.format(drivable=DRIVABLE, bbox=_join(bbox))
    area = _download(args.name, "area", area_query, args.refresh)
    lat, lon = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    skyline_query = SKYLINE_QUERY.format(
        radius=SKYLINE_RADIUS_M, lat=lat, lon=lon, min_height=SKYLINE_MIN_HEIGHT_M
    )
    tall = _download(args.name, "skyline", skyline_query, args.refresh)
    try:
        city = build_city_map(args.name, title, area, bbox, size=args.size, tall=tall)
    except MapError as exc:
        sys.exit(f"Cannot make a map of {args.name}: {exc}")

    city = city.model_copy(update={"tile_zooms": _download_tiles(bbox)})
    out = MAPS / f"{args.name}.json"
    out.write_text(city.model_dump_json(indent=1) + "\n", encoding="utf-8")
    features = map_features(area, city)
    drawn = MAPS / "features" / f"{args.name}.json"
    drawn.parent.mkdir(exist_ok=True)
    drawn.write_text(json.dumps(features, separators=(",", ":")), encoding="utf-8")
    counts = ", ".join(f"{len(items)} {kind}" for kind, items in features.items())
    print(f"Wrote {drawn.relative_to(ROOT)}: {counts}")
    print(
        f"Wrote {out.relative_to(ROOT)}: {city.width} x {city.height} junctions, "
        f"{len(city.streets)} streets, {len(city.skyline)} skyline towers"
    )
    print("  north-south: " + ", ".join(line.name for line in city.columns))
    print("  east-west:   " + ", ".join(line.name for line in city.rows))
    print(f"Use it: add  maps/{args.name}.json  to a game's maps: in racers.yaml")


def _area(name: str, bbox: str | None, title: str | None) -> tuple[str, Bbox]:
    if bbox:
        south, west, north, east = (float(part) for part in bbox.split(","))
        return title or name, (south, west, north, east)
    areas = yaml.safe_load((MAPS / "areas.yaml").read_text(encoding="utf-8"))
    if name not in areas:
        sys.exit(f"Unknown area {name!r}: pass --bbox, or pick one of {', '.join(sorted(areas))}")
    south, west, north, east = areas[name]["bbox"]
    return title or areas[name]["title"], (south, west, north, east)


def _download(name: str, kind: str, query: str, refresh: bool) -> list[dict[str, Any]]:
    """Run an Overpass query, caching the answer under maps/.cache/."""
    cache = MAPS / ".cache" / f"{name}.{kind}.json"
    if cache.exists() and not refresh:
        return list(json.loads(cache.read_text(encoding="utf-8"))["elements"])
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt, endpoint in enumerate(ENDPOINTS * 2):
        request = urllib.request.Request(
            endpoint, data=body, headers={"User-Agent": "inference-rally-map-fetch/0.1"}
        )
        try:
            print(f"Downloading {kind} from {urllib.parse.urlparse(endpoint).netloc} ...")
            with urllib.request.urlopen(request, timeout=200) as response:
                text = response.read().decode("utf-8")
            break
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  failed ({exc}); trying again")
            time.sleep(5 * (attempt + 1))  # the public servers ask callers to back off
    else:
        sys.exit("OpenStreetMap download failed; check the internet connection and try again")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return list(json.loads(text)["elements"])


def _download_tiles(bbox: Bbox) -> list[int]:
    """Fetch the area's map tiles that are not on disk yet; returns the zooms fully present."""
    complete = []
    for zoom in TILE_ZOOMS:
        xs, ys = tile_range(bbox, zoom, pad=TILE_PAD)
        missing = 0
        for x in xs:
            for y in ys:
                path = MAPS / "tiles" / str(zoom) / str(x) / f"{y}.png"
                if path.exists():
                    continue
                request = urllib.request.Request(
                    TILE_URL.format(z=zoom, x=x, y=y), headers={"User-Agent": USER_AGENT}
                )
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        data = response.read()
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"  map tile {zoom}/{x}/{y} failed ({exc})")
                    missing += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                time.sleep(TILE_PAUSE_S)
        print(f"Map tiles at zoom {zoom}: {len(xs) * len(ys) - missing} of {len(xs) * len(ys)}")
        if not missing:
            complete.append(zoom)
    return complete


def _join(bbox: Bbox) -> str:
    return ",".join(str(value) for value in bbox)


if __name__ == "__main__":
    main()
