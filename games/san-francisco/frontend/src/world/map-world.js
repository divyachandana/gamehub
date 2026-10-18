/**
 * The race on the real map, seen like a navigation app. Near the race the map is drawn from
 * OpenStreetMap data, so it stays sharp up close: land, parks, white streets with their
 * names, and light grey buildings at their real heights. Farther out, OpenStreetMap's map
 * pictures fill the view to the horizon. Barricades close this race's closed streets.
 * Junctions sit at the real intersections (``setJunctionLines``), so cars drive the real
 * streets. Data and pictures come from ``scripts/fetch_map.py``.
 */

import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { MAP_VIEW } from '../config.js';
import { junctionToWorld } from '../geometry.js';
import { buildBarricades, buildFinishGate, buildStartLine } from './maze-builder.js';

const TILE_PX = 256;
const TILE_PAD = 1; // the extra ring scripts/fetch_map.py downloads around the area
const METERS_PER_DEGREE_LAT = 110574;
const METERS_PER_DEGREE_LON = 111320;
const BARRICADE_FROM_JUNCTION = 7; // just past the crossing street's sidewalk
const FAR_GROUND = 8000;
const LAND_MARGIN = 40; // drawn land reaches this far past the drawn streets
const ROAD_EDGE = 1.2;
const JOIN_SEGMENTS = 10;
const LABEL_HEIGHT = 2.4;
const LABEL_EVERY = 120; // world units of street between two name labels
const LABEL_FONT = 'bold 64px Roboto, Arial, sans-serif';
// Ground layers draw in this order without depth, so they never flicker against each other.
const ORDER = { far: -0.95, pictures: -0.9, land: -0.8, parks: -0.7, edges: -0.6, roads: -0.5, labels: -0.4 };
const drawnCache = new Map(); // map name -> Promise of the drawn map's parts (or null)

/**
 * World X of each junction column and distance north of each row, at real spacing.
 * @param {object} city Street map.
 * @returns {[number[], number[]]}
 */
export function realJunctionLines(city) {
  const scale = MAP_VIEW.unitsPerMeter;
  const line = (lines) => lines.map((l) => (l.offset_m - lines[0].offset_m) * scale);
  return [line(city.columns), line(city.rows)];
}

/**
 * Build the map world.
 * @param {object} maze Maze view with ``city``.
 * @param {import('./geo.js').GeoFrame} geo Built without squeezing: real distances.
 * @returns {{group: THREE.Group, update: (time: number) => void, exitPosition: THREE.Vector3,
 *            isOpen: (x: number, z: number) => boolean}}
 */
export function buildMapWorld(maze, geo) {
  const group = new THREE.Group();
  group.name = 'map';
  group.add(farGround(junctionToWorld([(maze.width - 1) / 2, (maze.height - 1) / 2])));
  group.add(mapPictures(maze.city, geo, MAP_VIEW.pictureZoom));
  group.add(drawnMap(maze.city));
  group.add(buildStartLine(maze));
  const finish = buildFinishGate(maze);
  group.add(finish.group);
  if (maze.closed?.length) group.add(buildBarricades(closedPlacements(maze.closed)));
  return {
    group,
    exitPosition: junctionToWorld(maze.exit),
    isOpen: () => true, // the camera flies above the street: nothing to dodge
    update(time) {
      finish.update(time);
    },
  };
}

// --------------------------------------------------------------------------- far away

/** Plain map-colored ground out to the horizon. */
function farGround(center) {
  const plane = new THREE.PlaneGeometry(FAR_GROUND, FAR_GROUND).rotateX(-Math.PI / 2);
  const mesh = flat(plane, MAP_VIEW.groundColor, ORDER.far);
  mesh.position.set(center.x, -0.1, center.z);
  return mesh;
}

/** OpenStreetMap's map pictures stitched into one texture on a plane, north up. */
function mapPictures(city, geo, zoom) {
  const { x0, y0, x1, y1 } = tileRange(city.bbox, zoom);
  const canvas = document.createElement('canvas');
  canvas.width = (x1 - x0 + 1) * TILE_PX;
  canvas.height = (y1 - y0 + 1) * TILE_PX;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = `#${MAP_VIEW.groundColor.toString(16).padStart(6, '0')}`;
  ctx.fillRect(0, 0, canvas.width, canvas.height); // opaque, so it keeps its place in the draw order
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  for (let x = x0; x <= x1; x += 1) {
    for (let y = y0; y <= y1; y += 1) {
      const image = new Image();
      image.onload = () => {
        ctx.drawImage(image, (x - x0) * TILE_PX, (y - y0) * TILE_PX);
        texture.needsUpdate = true;
      };
      image.src = `/api/tiles/${zoom}/${x}/${y}.png`;
    }
  }

  const [north, west] = tileCorner(x0, y0, zoom);
  const [south, east] = tileCorner(x1 + 1, y1 + 1, zoom);
  const scale = MAP_VIEW.unitsPerMeter;
  const width = (east - west) * METERS_PER_DEGREE_LON * Math.cos((geo.lat0 * Math.PI) / 180) * scale;
  const depth = (north - south) * METERS_PER_DEGREE_LAT * scale;
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(width, depth).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ map: texture, depthWrite: false }),
  );
  mesh.position.copy(geo.toWorld((north + south) / 2, (west + east) / 2, -0.05));
  mesh.rotation.y = -geo.rotation; // the world runs along the street grid; the pictures run north
  mesh.renderOrder = ORDER.pictures;
  return mesh;
}

/** Tiles covering the area at ``zoom``, with the same padding the download used. */
function tileRange([south, west, north, east], zoom) {
  const count = 2 ** zoom;
  const x = (lon) => Math.floor(((lon + 180) / 360) * count);
  const y = (lat) => Math.floor(((1 - Math.asinh(Math.tan((lat * Math.PI) / 180)) / Math.PI) / 2) * count);
  return { x0: x(west) - TILE_PAD, x1: x(east) + TILE_PAD, y0: y(north) - TILE_PAD, y1: y(south) + TILE_PAD };
}

/** Latitude/longitude of a tile's north-west corner. */
function tileCorner(x, y, zoom) {
  const count = 2 ** zoom;
  return [(Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / count))) * 180) / Math.PI, (x / count) * 360 - 180];
}

// --------------------------------------------------------------------------- drawn map

/** Land, parks, streets, names and buildings; loaded once per map, shown when ready. */
function drawnMap(city) {
  if (!drawnCache.has(city.name)) drawnCache.set(city.name, loadDrawnMap(city));
  const group = new THREE.Group();
  drawnCache.get(city.name).then((parts) => {
    if (!parts) return;
    group.add(flat(parts.land, MAP_VIEW.landColor, ORDER.land));
    if (parts.parks) group.add(flat(parts.parks, MAP_VIEW.parkColor, ORDER.parks));
    group.add(flat(parts.edges, MAP_VIEW.roadEdgeColor, ORDER.edges), flat(parts.roads, 0xffffff, ORDER.roads));
    for (const { geometry, texture } of parts.labels) {
      const label = new THREE.Mesh(
        geometry,
        new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false }),
      );
      label.renderOrder = ORDER.labels;
      group.add(label);
    }
    const buildings = new THREE.Mesh(
      parts.buildings,
      new THREE.MeshLambertMaterial({ color: MAP_VIEW.buildingColor, emissive: MAP_VIEW.buildingShadeLift }),
    );
    group.add(buildings);
  });
  return group;
}

async function loadDrawnMap(city) {
  const response = await fetch(`/api/features/${city.name}.json`);
  if (!response.ok) return null;
  const features = await response.json();
  const scale = MAP_VIEW.unitsPerMeter;
  const [originX, originY] = [city.columns[0].offset_m, city.rows[0].offset_m];
  // Shape coordinates: x east, y north (world -Z); laid flat by rotateX(-PI/2).
  const toPlane = (coords) => {
    const points = [];
    for (let k = 0; k < coords.length; k += 2) {
      points.push(new THREE.Vector2((coords[k] - originX) * scale, (coords[k + 1] - originY) * scale));
    }
    return points;
  };

  const roads = features.roads.map(([kind, name, ...coords]) => ({
    name,
    points: toPlane(coords),
    width: MAP_VIEW.roadWidths[kind] ?? MAP_VIEW.roadWidths.default,
  }));
  const parks = features.parks.map((coords) => new THREE.ShapeGeometry(new THREE.Shape(toPlane(coords))));
  const buildings = features.buildings.map(([height, ...coords]) =>
    new THREE.ExtrudeGeometry(new THREE.Shape(toPlane(coords)), { depth: height * scale, bevelEnabled: false }),
  );
  return {
    land: landPlane(roads),
    parks: parks.length ? mergeGeometries(parks).rotateX(-Math.PI / 2) : null,
    edges: ribbons(roads, ROAD_EDGE),
    roads: ribbons(roads, 0),
    labels: streetLabels(roads),
    buildings: mergeGeometries(buildings).rotateX(-Math.PI / 2), // height becomes +Y
  };
}

/** Land under the drawn streets (it covers the map pictures there). */
function landPlane(roads) {
  const all = roads.flatMap((road) => road.points);
  const [minX, maxX] = [Math.min(...all.map((p) => p.x)), Math.max(...all.map((p) => p.x))];
  const [minY, maxY] = [Math.min(...all.map((p) => p.y)), Math.max(...all.map((p) => p.y))];
  const width = maxX - minX + 2 * LAND_MARGIN;
  const depth = maxY - minY + 2 * LAND_MARGIN;
  return new THREE.PlaneGeometry(width, depth).translate((minX + maxX) / 2, (minY + maxY) / 2, 0).rotateX(-Math.PI / 2);
}

/**
 * Streets as flat ribbons with round joins, all in one geometry.
 * @param {{points: THREE.Vector2[], width: number}[]} roads
 * @param {number} extra Added to every width (the darker edge drawn under the street).
 */
function ribbons(roads, extra) {
  const positions = [];
  for (const { points, width } of roads) {
    const half = (width + extra) / 2;
    for (let k = 0; k < points.length - 1; k += 1) {
      const [a, b] = [points[k], points[k + 1]];
      const length = a.distanceTo(b);
      if (!length) continue;
      const nx = (-(b.y - a.y) / length) * half;
      const ny = ((b.x - a.x) / length) * half;
      positions.push(a.x + nx, a.y + ny, 0, b.x + nx, b.y + ny, 0, b.x - nx, b.y - ny, 0);
      positions.push(a.x + nx, a.y + ny, 0, b.x - nx, b.y - ny, 0, a.x - nx, a.y - ny, 0);
    }
    for (const point of points) {
      for (let k = 0; k < JOIN_SEGMENTS; k += 1) {
        const [t0, t1] = [(k / JOIN_SEGMENTS) * Math.PI * 2, ((k + 1) / JOIN_SEGMENTS) * Math.PI * 2];
        positions.push(point.x, point.y, 0);
        positions.push(point.x + Math.cos(t0) * half, point.y + Math.sin(t0) * half, 0);
        positions.push(point.x + Math.cos(t1) * half, point.y + Math.sin(t1) * half, 0);
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  return geometry.rotateX(-Math.PI / 2);
}

/** Street names written along the streets, every LABEL_EVERY units; one mesh per name. */
function streetLabels(roads) {
  const byName = new Map();
  for (const road of roads) {
    if (!road.name) continue;
    if (!byName.has(road.name)) byName.set(road.name, []);
    byName.get(road.name).push(road);
  }
  const labels = [];
  for (const [name, ways] of byName) {
    const { texture, aspect } = labelTexture(name);
    const width = LABEL_HEIGHT * aspect;
    const planes = [];
    let untilNext = LABEL_EVERY / 2;
    for (const { points } of ways) {
      for (let k = 0; k < points.length - 1; k += 1) {
        const [a, b] = [points[k], points[k + 1]];
        const length = a.distanceTo(b);
        untilNext -= length;
        if (untilNext > 0 || length < width * 1.3) continue;
        untilNext = LABEL_EVERY;
        let angle = Math.atan2(b.y - a.y, b.x - a.x);
        const backwards = Math.cos(angle) < -1e-3 || (Math.abs(Math.cos(angle)) <= 1e-3 && Math.sin(angle) < 0);
        if (backwards) angle += Math.PI; // read left to right, or upward
        const plane = new THREE.PlaneGeometry(width, LABEL_HEIGHT).rotateZ(angle);
        planes.push(plane.translate((a.x + b.x) / 2, (a.y + b.y) / 2, 0));
      }
    }
    if (planes.length) labels.push({ geometry: mergeGeometries(planes).rotateX(-Math.PI / 2), texture });
  }
  return labels;
}

function labelTexture(text) {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  ctx.font = LABEL_FONT;
  canvas.width = Math.ceil(ctx.measureText(text).width) + 24;
  canvas.height = 80;
  ctx.font = LABEL_FONT;
  ctx.fillStyle = MAP_VIEW.labelColor;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 2);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  return { texture, aspect: canvas.width / canvas.height };
}

/** A flat, unlit ground layer drawn at its place in ORDER. */
function flat(geometry, color, order) {
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color, depthWrite: false, side: THREE.DoubleSide }));
  mesh.renderOrder = order;
  return mesh;
}

// --------------------------------------------------------------------------- closures

/** A barricade near each end of every closed street, across it. */
function closedPlacements(closed) {
  return closed.flatMap(([a, b]) => {
    const [from, to] = [junctionToWorld(a), junctionToWorld(b)];
    const direction = to.clone().sub(from).normalize();
    const kind = a[1] === b[1] ? 'ew' : 'ns';
    return [
      { position: from.addScaledVector(direction, BARRICADE_FROM_JUNCTION), kind },
      { position: to.addScaledVector(direction, -BARRICADE_FROM_JUNCTION), kind },
    ];
  });
}
