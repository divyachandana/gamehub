// Turn an Inference Rally street map (maps/sf-*.json, built from OpenStreetMap)
// into an Alibi Breaker map: street corners with positions on the map tiles,
// and walkable street segments with real names and walking minutes.
//
//   node scripts/import-rally-map.mjs <path/to/Inference-Rally/maps/sf-north-beach.json> [--extend lat,lon;lat,lon]
//
// --extend widens the visible map to include off-grid spots (piers, parks...)
// that a case places at exact coordinates.
//
// Map tiles: copy Inference-Rally/maps/tiles into maps/tiles.

import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ZOOM = 17; // tile zoom the map is drawn at
const TILE_PX = 256;
const MARGIN_M = 70; // map border around the outer streets
const WALK_M_PER_MIN = 80; // ~4.8 km/h
const M_PER_DEG_LAT = 110_574;
const M_PER_DEG_LON = 111_320;

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = process.argv[2];
const extendArg = process.argv.indexOf("--extend");
const extra = extendArg > 0 ? process.argv[extendArg + 1].split(";").map((pt) => pt.split(",").map(Number)) : [];
if (!source) {
  console.error("Usage: node scripts/import-rally-map.mjs <Inference-Rally map json>");
  process.exit(1);
}
const rally = JSON.parse(await readFile(source, "utf8"));
const [lat0, lon0] = rally.origin;
const rot = (rally.rotation_deg * Math.PI) / 180;

// Inverse of Inference Rally's Frame.project: grid meters -> lat/lon.
function toLatLon(x, y) {
  const east = x * Math.cos(rot) - y * Math.sin(rot);
  const north = x * Math.sin(rot) + y * Math.cos(rot);
  return [lat0 + north / M_PER_DEG_LAT, lon0 + east / (M_PER_DEG_LON * Math.cos((lat0 * Math.PI) / 180))];
}

// Web Mercator world pixels at ZOOM.
function toPixels(lat, lon) {
  const world = TILE_PX * 2 ** ZOOM;
  const sin = Math.sin((lat * Math.PI) / 180);
  return [((lon + 180) / 360) * world, (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * world];
}

const short = (name) =>
  name.replace(/ Street$/, " St").replace(/ Avenue$/, " Ave").replace(/ Boulevard$/, " Blvd");

const nodes = [];
for (const [x, col] of rally.columns.entries()) {
  for (const [y, row] of rally.rows.entries()) {
    const [lat, lon] = toLatLon(col.offset_m, row.offset_m);
    const [px, py] = toPixels(lat, lon);
    nodes.push({ id: `j_${x}_${y}`, name: `${short(col.name)} & ${short(row.name)}`, streets: [col.name, row.name], px, py, lat, lon });
  }
}

const metersPerPx = (156543.03392 * Math.cos((lat0 * Math.PI) / 180)) / 2 ** ZOOM;
const margin = MARGIN_M / metersPerPx;
const bounds = [...nodes.map((n) => [n.px, n.py]), ...extra.map(([lat, lon]) => toPixels(lat, lon))];
const left = Math.min(...bounds.map((b) => b[0])) - margin;
const top = Math.min(...bounds.map((b) => b[1])) - margin;
const right = Math.max(...bounds.map((b) => b[0])) + margin;
const bottom = Math.max(...bounds.map((b) => b[1])) + margin;

const roads = rally.streets.map(([[x1, y1], [x2, y2]]) => {
  const sameRow = y1 === y2;
  const meters = sameRow
    ? Math.abs(rally.columns[x2].offset_m - rally.columns[x1].offset_m)
    : Math.abs(rally.rows[y2].offset_m - rally.rows[y1].offset_m);
  return {
    from: `j_${x1}_${y1}`,
    to: `j_${x2}_${y2}`,
    name: sameRow ? rally.rows[y1].name : rally.columns[x1].name,
    meters: Math.round(meters),
    minutes: Math.round((meters / WALK_M_PER_MIN) * 10) / 10,
  };
});

const out = {
  id: rally.name,
  name: rally.title,
  attribution: "Map © OpenStreetMap contributors",
  travel: { mode: "walk", metersPerMinute: WALK_M_PER_MIN },
  width: Math.round(right - left),
  height: Math.round(bottom - top),
  tiles: { zoom: ZOOM, left: Math.round(left), top: Math.round(top) },
  corners: nodes.map((n) => ({
    id: n.id,
    name: n.name,
    streets: n.streets,
    x: Math.round((n.px - left) * 10) / 10,
    y: Math.round((n.py - top) * 10) / 10,
    lat: Math.round(n.lat * 1e6) / 1e6,
    lon: Math.round(n.lon * 1e6) / 1e6,
  })),
  roads,
};
const file = path.join(root, "maps", `${rally.name}.json`);
await writeFile(file, JSON.stringify(out, null, 1) + "\n");
console.log(`Wrote ${path.relative(root, file)}: ${out.corners.length} corners, ${roads.length} street segments, ${out.width}x${out.height}px`);
