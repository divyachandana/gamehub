// Loads case files from cases/*.json and the street map each one points at
// (maps/<name>.json, made from OpenStreetMap by scripts/import-rally-map.mjs).
// A case puts its places (Joe's Café, the jewelers...) on real street corners.
// Case files are re-read on every new game, so you can edit and click "New game".

import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildGraph } from "./map.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const casesDir = path.join(root, "cases");
const mapsDir = path.join(root, "maps");
const SAFE_ID = /^[a-z0-9][a-z0-9-]*$/;

async function readJson(file) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch (err) {
    throw new Error(`Can't read ${path.relative(root, file)}: ${err.message}`);
  }
}

export async function listCases() {
  const files = (await readdir(casesDir)).filter((f) => f.endsWith(".json")).sort();
  const cases = [];
  for (const file of files) {
    try {
      const c = await readJson(path.join(casesDir, file));
      cases.push({
        id: c.id,
        title: c.title,
        category: c.category ?? "Case",
        tagline: c.tagline ?? "",
        icon: c.icon ?? "🕵️",
        area: c.area ?? "",
        subjects: c.suspects?.length ?? 0,
        evidence: c.evidence?.length ?? 0,
      });
    } catch (err) {
      console.warn(`Skipping ${file}: ${err.message}`);
    }
  }
  return cases;
}

const WALK_DETOUR = 1.3; // paths are rarely straight lines

// Web Mercator, matching how the map tiles were cut (see scripts/import-rally-map.mjs).
function project(map, lat, lon) {
  const world = 256 * 2 ** map.tiles.zoom;
  const sin = Math.sin((lat * Math.PI) / 180);
  const px = ((lon + 180) / 360) * world;
  const py = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * world;
  return [Math.round((px - map.tiles.left) * 10) / 10, Math.round((py - map.tiles.top) * 10) / 10];
}

function metersBetween(lat1, lon1, lat2, lon2) {
  const k = Math.PI / 180;
  const dy = (lat2 - lat1) * 110_574;
  const dx = (lon2 - lon1) * 111_320 * Math.cos(((lat1 + lat2) / 2) * k);
  return Math.hypot(dx, dy);
}

function nearestCorner(map, connected, lat, lon) {
  let best = null;
  for (const c of map.corners) {
    if (!connected.has(c.id)) continue;
    const d = metersBetween(lat, lon, c.lat, c.lon);
    if (!best || d < best.d) best = { c, d };
  }
  return best?.c;
}

const norm = (s) => s.toLowerCase().replace(/\b(street|st|avenue|ave)\b\.?/g, "").replace(/\s+/g, " ").trim();

function findCorner(map, corner) {
  if (typeof corner === "string") return map.corners.find((c) => c.id === corner);
  const [a, b] = corner.map(norm);
  return map.corners.find((c) => {
    const streets = c.streets.map(norm);
    return streets.includes(a) && streets.includes(b);
  });
}

export async function loadCase(id) {
  if (!SAFE_ID.test(id ?? "")) throw new Error(`Bad case id "${id}"`);
  const data = await readJson(path.join(casesDir, `${id}.json`));
  if (!SAFE_ID.test(data.map ?? "")) throw new Error(`Case "${id}": bad map id "${data.map}"`);
  const map = await readJson(path.join(mapsDir, `${data.map}.json`));

  const problems = [];
  const connected = new Set(map.roads.flatMap((r) => [r.from, r.to]));
  const places = data.places.map((p) => {
    if (p.at) {
      // An off-grid spot (a pier, a campus...) at exact coordinates, walked to from the
      // nearest street corner (or the one named in "corner").
      const [lat, lon] = p.at;
      const corner = p.corner ? findCorner(map, p.corner) : nearestCorner(map, connected, lat, lon);
      if (!corner) problems.push(`place ${p.id}: no corner ${JSON.stringify(p.corner)} on ${map.name}`);
      const [x, y] = project(map, lat, lon);
      const meters = corner ? metersBetween(lat, lon, corner.lat, corner.lon) * WALK_DETOUR : 0;
      const link = Math.round((meters / (map.travel?.metersPerMinute ?? 80)) * 10) / 10;
      return { ...p, cornerId: corner?.id, cornerName: corner ? `near ${corner.name}` : "", x, y, link };
    }
    const corner = findCorner(map, p.corner);
    if (!corner) problems.push(`place ${p.id}: no corner ${JSON.stringify(p.corner)} on ${map.name}`);
    return { ...p, cornerId: corner?.id, cornerName: corner?.name, x: corner?.x, y: corner?.y, link: 0 };
  });
  if (problems.length) throw new Error(`Case "${id}" has problems:\n- ${problems.join("\n- ")}`);

  // Places hang off their corner, so routes run on real streets.
  const nodes = [...map.corners, ...places];
  const roads = [...map.roads, ...places.map((p) => ({ from: p.id, to: p.cornerId, minutes: p.link, name: p.path ?? null }))];

  // Footpaths between nearby off-grid places ("paths": [["pier2", "cafe"], ...]).
  const latLon = (p) => p.at ?? [map.corners.find((c) => c.id === p.cornerId).lat, map.corners.find((c) => c.id === p.cornerId).lon];
  for (const [a, b] of data.paths ?? []) {
    const [pa, pb] = [places.find((p) => p.id === a), places.find((p) => p.id === b)];
    if (!pa || !pb) {
      problems.push(`path ${a} - ${b}: unknown place`);
      continue;
    }
    const meters = metersBetween(...latLon(pa), ...latLon(pb)) * WALK_DETOUR;
    roads.push({ from: a, to: b, minutes: Math.round((meters / (map.travel?.metersPerMinute ?? 80)) * 10) / 10, name: "footpath" });
  }
  if (problems.length) throw new Error(`Case "${id}" has problems:\n- ${problems.join("\n- ")}`);

  const c = {
    labels: { role: "Detective", subjects: "Suspects", accuse: "Accuse", win: "Case closed", wrong: "Wrong suspect" },
    ...data,
    map: { ...map, places },
    graph: buildGraph({ places: nodes, roads }),
    places: new Map(places.map((p) => [p.id, p])),
  };
  c.watch ??= { place: c.crime.place, window: c.crime.window, label: c.crime.label };
  validate(c);
  return c;
}

function validate(c) {
  const problems = [];
  const place = (id, where) => {
    if (id != null && !c.places.has(id)) problems.push(`${where}: unknown place "${id}"`);
  };
  const suspects = new Set(c.suspects.map((s) => s.id));
  place(c.crime.place, "crime");
  place(c.watch.place, "watch");
  for (const s of c.suspects) {
    place(s.home, `suspect ${s.id} home`);
    for (const key of ["name", "truth", "cover", "pressure"]) {
      if (!s[key]) problems.push(`suspect ${s.id}: missing "${key}"`);
    }
  }
  if (c.suspects.filter((s) => s.guilty).length !== 1) problems.push("exactly one suspect must have guilty: true");
  for (const e of c.evidence) {
    for (const f of e.facts ?? []) {
      place(f.place, `evidence ${e.id}`);
      if (f.suspect != null && !suspects.has(f.suspect)) problems.push(`evidence ${e.id}: unknown suspect "${f.suspect}"`);
      if (!/^\d{2}:\d{2}$/.test(f.time)) problems.push(`evidence ${e.id}: time must be HH:MM (24h)`);
    }
  }
  if (problems.length) throw new Error(`Case "${c.id}" has problems:\n- ${problems.join("\n- ")}`);
}

// What the browser is allowed to see: no truths, cover stories or solution.
export function publicCase(c) {
  return {
    id: c.id,
    title: c.title,
    category: c.category,
    icon: c.icon,
    area: c.area,
    labels: c.labels,
    briefing: c.briefing,
    crime: { place: c.crime.place, time: c.crime.time, label: c.crime.label, icon: c.crime.icon },
    map: {
      name: c.map.name,
      attribution: c.map.attribution,
      travel: c.map.travel,
      width: c.map.width,
      height: c.map.height,
      tiles: c.map.tiles,
      corners: c.map.corners.map(({ id, name, x, y }) => ({ id, name, x, y })),
      places: c.map.places.map(({ id, name, icon, cornerName, x, y }) => ({ id, name, icon, cornerName, x, y })),
    },
    suspects: c.suspects.map(({ id, name, role, color, bio, avatar, hat }) => ({ id, name, role, color, bio, avatar, hat })),
    evidence: c.evidence,
  };
}
