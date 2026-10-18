/**
 * San Francisco around the maze, placed by real latitude/longitude: the city's land and the
 * bay, the hills, the Golden Gate and Bay bridges, the downtown skyline from the street
 * map's tall buildings (Transamerica Pyramid, Salesforce Tower), Coit Tower, Sutro Tower,
 * Alcatraz and a fog bank rolling in through the Golden Gate.
 *
 * Positions are real; sizes are exaggerated (see ``realHeight``) so landmarks read from
 * the street.
 */

import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { BLOCK, COLORS, HILLS, LANDMARKS, SHORE, WORLD } from '../config.js';
import { realFootprint, realHeight } from './geo.js';
import {
  cityGroundTexture,
  cloudTexture,
  repeatEvery4,
  stripeTexture,
  towerTexture,
  waterTexture,
} from './textures.js';

const WATER_Y = -0.6;
const LAND_Y = -0.25; // well below the road tiles, so roads never flicker through
const MIN_TOWER_SIDE = 8;
const SPECIAL_TOWERS = ['Transamerica Pyramid', 'Salesforce Tower']; // have their own models

const GOLDEN_GATE = Object.freeze({ span: 240, side: 90, towerHeight: 115, deckHeight: 32, deckWidth: 14 });
const BAY_BRIDGE = Object.freeze({ span: 150, side: 70, towerHeight: 70, deckHeight: 26, deckWidth: 16 });

/**
 * Build the bay area around the maze.
 * @param {THREE.Vector3} center World center of the maze.
 * @param {import('./geo.js').GeoFrame} geo Places latitude/longitude in the world.
 * @param {object|null} city Street map (for its skyline), or null.
 * @returns {{group: THREE.Group, update: (time: number) => void}}
 */
export function buildBayArea(center, geo, city) {
  const parts = new THREE.Group();
  const shore = SHORE.map(([lat, lon]) => geo.toWorld(lat, lon));
  parts.add(water(center), land(shore));
  for (const [lat, lon, radius, height] of HILLS) parts.add(hill(geo.toWorld(lat, lon), radius, height));

  const goldenGate = suspensionBridge(geo, LANDMARKS.goldenGateTowers, GOLDEN_GATE, COLORS.goldenGate);
  parts.add(goldenGate, suspensionBridge(geo, LANDMARKS.bayBridgeTowers, BAY_BRIDGE, 0xb9bec4));
  parts.add(skyline(geo, city?.skyline ?? [], shore));
  parts.add(transamericaPyramid(geo.toWorld(...LANDMARKS.transamerica)));
  parts.add(salesforceTower(geo.toWorld(...LANDMARKS.salesforce)));
  parts.add(coitTower(geo.toWorld(...LANDMARKS.coitTower)));
  parts.add(sutroTower(geo.toWorld(...LANDMARKS.sutroTower), geo));
  parts.add(alcatraz(geo.toWorld(...LANDMARKS.alcatraz)));

  // Four viewports draw the scenery every frame: one draw call per look, not per strut.
  const group = mergeByLook(parts);
  const fog = fogBank(goldenGate.position);
  group.add(fog);
  return {
    group,
    update(time) {
      // Karl the Fog creeps slowly back and forth through the Golden Gate.
      fog.children.forEach((puff, i) => {
        puff.position.x = puff.userData.baseX + Math.sin(time * 0.05 + i * 1.3) * 25;
      });
    },
  };
}

// --------------------------------------------------------------------------- ground

function water(center) {
  const size = WORLD.groundSize;
  const plane = flatMesh(new THREE.PlaneGeometry(size, size, 32, 32), waterTexture(size / 16));
  plane.position.set(center.x, WATER_Y, center.z);
  return plane;
}

/** The city's land, cut out along its real shoreline. */
function land(shore) {
  // Shape Y is world north (-Z); the plane's rotation below lays it flat.
  const shape = new THREE.Shape(shore.map((point) => new THREE.Vector2(point.x, -point.z)));
  const plane = flatMesh(new THREE.ShapeGeometry(shape), cityGroundTexture(1 / BLOCK, 1 / BLOCK));
  plane.position.y = LAND_Y;
  plane.receiveShadow = true;
  return plane;
}

/** Ground pushed back with a polygon offset, so roads never flicker through on any GPU. */
function flatMesh(geometry, map) {
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshLambertMaterial({ map, polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 4 }),
  );
  mesh.rotation.x = -Math.PI / 2;
  return mesh;
}

function hill(position, radius, height) {
  const mesh = new THREE.Mesh(
    new THREE.ConeGeometry(radius, height, 7, 1),
    new THREE.MeshLambertMaterial({ color: COLORS.hills[Math.abs(Math.round(position.x)) % 4], flatShading: true }),
  );
  mesh.position.set(position.x, height / 2 - 4, position.z);
  mesh.rotation.y = position.x + position.z; // break up identical silhouettes
  return mesh;
}

/** Height of the ground at ``position`` on the hills (0 on flat land). */
function groundHeight(position, geo) {
  let top = 0;
  for (const [lat, lon, radius, height] of HILLS) {
    const distance = geo.toWorld(lat, lon).distanceTo(position.clone().setY(0));
    top = Math.max(top, (height - 4) * (1 - distance / radius));
  }
  return top;
}

// --------------------------------------------------------------------------- bridges

/**
 * Suspension bridge from its first real tower toward its second: towers ``span`` apart,
 * side spans down to the anchorages, two main cables and the suspender lines.
 */
function suspensionBridge(geo, [first, second], spec, color) {
  const from = geo.toWorld(...first);
  const direction = geo.toWorld(...second).sub(from).setY(0).normalize();
  const group = new THREE.Group();
  group.position.copy(from).addScaledVector(direction, spec.span / 2).setY(WATER_Y);
  group.rotation.y = Math.atan2(-direction.z, direction.x); // local +X runs from tower to tower

  const paint = lambert(color);
  const half = spec.span / 2 + spec.side;
  group.add(box(half * 2, 3, spec.deckWidth, paint, 0, spec.deckHeight, 0));
  for (const x of [-spec.span / 2, spec.span / 2]) group.add(bridgeTower(x, spec, paint));

  const suspenders = [];
  for (const z of [-spec.deckWidth / 2, spec.deckWidth / 2]) {
    const points = [];
    for (let x = -half; x <= half; x += 10) points.push(new THREE.Vector3(x, cableHeight(x, spec), z));
    const curve = new THREE.CatmullRomCurve3(points, false, 'centripetal');
    group.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 120, 0.9, 5), paint));
    for (let x = -half + 12; x < half; x += 12) {
      if (Math.abs(Math.abs(x) - spec.span / 2) > 6) suspenders.push(x, spec.deckHeight + 1.5, z, x, cableHeight(x, spec), z);
    }
  }
  const lines = new THREE.BufferGeometry();
  lines.setAttribute('position', new THREE.Float32BufferAttribute(suspenders, 3));
  group.add(new THREE.LineSegments(lines, new THREE.LineBasicMaterial({ color })));
  return group;
}

/** Art-deco tower: two tapering legs joined by portal struts, on a concrete pier. */
function bridgeTower(x, spec, paint) {
  const group = new THREE.Group();
  group.position.x = x;
  group.add(box(18, 10, spec.deckWidth + 12, lambert(0x9a958c), 0, 3, 0));
  const tiers = [7, 6, 5];
  const tierHeight = spec.towerHeight / tiers.length;
  for (const z of [-spec.deckWidth / 2, spec.deckWidth / 2]) {
    tiers.forEach((width, k) => group.add(box(width, tierHeight, width, paint, 0, tierHeight * (k + 0.5), z)));
  }
  for (const y of [0.42, 0.62, 0.8, 0.96]) group.add(box(4, 5, spec.deckWidth, paint, 0, spec.towerHeight * y, 0));
  return group;
}

/** Main cable height: a parabola over the main span, sagging down to the anchorages. */
function cableHeight(x, spec) {
  const top = spec.towerHeight - 2;
  const low = spec.deckHeight + 5;
  const tower = spec.span / 2;
  const ax = Math.abs(x);
  if (ax <= tower) return low + (top - low) * (ax / tower) ** 2;
  const t = (ax - tower) / spec.side;
  return THREE.MathUtils.lerp(top, spec.deckHeight + 2, t) - Math.sin(t * Math.PI) * 8;
}

// --------------------------------------------------------------------------- downtown

/** Tall buildings from the street map on the city's land, merged into one mesh per color. */
function skyline(geo, buildings, shore) {
  const byColor = new Map();
  for (const building of buildings) {
    const position = geo.toWorld(building.lat, building.lon);
    // Islands (Treasure Island) are not modelled; their towers would stand in the bay.
    if (SPECIAL_TOWERS.includes(building.name) || !inside(shore, position)) continue;
    const height = realHeight(building.height_m);
    const width = Math.max(MIN_TOWER_SIDE, realFootprint(building.width_m));
    const depth = Math.max(MIN_TOWER_SIDE, realFootprint(building.depth_m));
    const geometry = repeatEvery4(new THREE.BoxGeometry(width, height, depth), Math.max(width, depth), height);
    geometry.translate(position.x, height / 2, position.z);
    const color = Math.round(building.height_m) % COLORS.towers.length;
    if (!byColor.has(color)) byColor.set(color, []);
    byColor.get(color).push(geometry);
  }
  const group = new THREE.Group();
  for (const [color, geometries] of byColor) {
    const material = new THREE.MeshLambertMaterial({ map: towerTexture(COLORS.towers[color]) });
    group.add(new THREE.Mesh(mergeGeometries(geometries), material));
  }
  return group;
}

function transamericaPyramid(position) {
  const group = new THREE.Group();
  group.position.copy(position);
  const height = realHeight(260);
  const white = lambert(0xe9e6dc);
  const pyramid = new THREE.Mesh(new THREE.ConeGeometry(15, height, 4), white);
  pyramid.rotation.y = Math.PI / 4; // faces toward the compass points
  pyramid.position.y = height / 2;
  group.add(pyramid);
  for (const side of [-1, 1]) group.add(box(3, height * 0.22, 6, white, side * 4, height * 0.7, 0)); // the "wings"
  group.add(box(0.8, 12, 0.8, white, 0, height + 5, 0)); // spire
  return group;
}

function salesforceTower(position) {
  const group = new THREE.Group();
  group.position.copy(position);
  const height = realHeight(326);
  const body = repeatEvery4(new THREE.CylinderGeometry(9, 11.5, height, 8), 64, height);
  group.add(mesh(body, new THREE.MeshLambertMaterial({ map: towerTexture('#c3ccd3') }), 0, height / 2, 0));
  group.add(mesh(new THREE.CylinderGeometry(8.6, 9, 12, 8), lambert(0xe6eaec), 0, height + 6, 0)); // crown
  group.add(mesh(new THREE.CylinderGeometry(3, 8.6, 8, 8), lambert(0xe6eaec), 0, height + 16, 0));
  return group;
}

function coitTower(position) {
  const group = new THREE.Group();
  group.position.copy(position);
  const height = realHeight(64);
  const cream = lambert(0xefe9dc);
  group.add(mesh(new THREE.CylinderGeometry(3.2, 3.6, height, 12), cream, 0, height / 2, 0));
  group.add(mesh(new THREE.CylinderGeometry(3.8, 3.2, 2, 12), cream, 0, height + 1, 0));
  return group;
}

// --------------------------------------------------------------------------- hills and the bay

function sutroTower(position, geo) {
  const group = new THREE.Group();
  const base = groundHeight(position, geo) - 2;
  const height = realHeight(298);
  const paint = new THREE.MeshLambertMaterial({ map: stripeTexture('#d6402f', '#f2f2f2', 7) });
  const leg = (angle, radius, y) =>
    new THREE.Vector3(position.x + Math.cos(angle) * radius, y, position.z + Math.sin(angle) * radius);
  const angles = [0, (Math.PI * 2) / 3, (Math.PI * 4) / 3];
  for (const angle of angles) {
    group.add(beam(leg(angle, 9, base), leg(angle, 4.5, base + height * 0.8), 1.4, paint));
    group.add(beam(leg(angle, 4.5, base + height * 0.8), leg(angle, 9, base + height), 1, paint)); // antenna prongs
  }
  for (const [share, radius] of [
    [0.4, 6.9],
    [0.72, 5],
  ]) {
    const y = base + height * share;
    angles.forEach((angle, k) => group.add(beam(leg(angle, radius, y), leg(angles[(k + 1) % 3], radius, y), 1, paint)));
  }
  return group;
}

function alcatraz(position) {
  const group = new THREE.Group();
  group.position.copy(position);
  group.add(mesh(new THREE.CylinderGeometry(18, 24, 8, 9), lambert(0x8c7f6a), 0, WATER_Y + 2, 0));
  group.add(box(20, 7, 9, lambert(0xe8e2d0), -2, WATER_Y + 9, 0));
  group.add(mesh(new THREE.CylinderGeometry(1.2, 1.6, 16, 8), lambert(0xf2f2f2), 11, WATER_Y + 14, 4));
  return group;
}

function fogBank(bridge) {
  const group = new THREE.Group();
  const texture = cloudTexture();
  for (let i = 0; i < 9; i += 1) {
    const puff = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: texture, transparent: true, opacity: 0.85, depthWrite: false, fog: false }),
    );
    puff.position.set(bridge.x - 200 - (i % 3) * 60, 14 + (i % 3) * 9, bridge.z - 160 + i * 40);
    puff.scale.set(150, 34, 1);
    puff.userData.baseX = puff.position.x;
    group.add(puff);
  }
  return group;
}

// --------------------------------------------------------------------------- helpers

/** Bake every static mesh into one mesh per material look; lines stay as they are. */
function mergeByLook(parts) {
  parts.updateMatrixWorld(true);
  const looks = new Map();
  const group = new THREE.Group();
  parts.traverse((object) => {
    if (object.isLineSegments) {
      object.geometry.applyMatrix4(object.matrixWorld);
      group.add(new THREE.LineSegments(object.geometry, object.material));
    }
    if (!object.isMesh) return;
    const { material } = object;
    const key = `${material.color.getHex()}:${material.map?.uuid ?? ''}:${material.polygonOffset}`;
    if (!looks.has(key)) looks.set(key, { material, geometries: [] });
    looks.get(key).geometries.push(object.geometry.clone().applyMatrix4(object.matrixWorld));
  });
  for (const { material, geometries } of looks.values()) {
    const mesh = new THREE.Mesh(mergeGeometries(geometries), material);
    mesh.receiveShadow = material.polygonOffset; // the ground
    group.add(mesh);
  }
  return group;
}

/** Ray casting on the ground plane: inside if a ray crosses the outline an odd number of times. */
function inside(outline, point) {
  let result = false;
  outline.forEach((a, k) => {
    const b = outline[(k + 1) % outline.length];
    if (a.z > point.z !== b.z > point.z && point.x < a.x + ((point.z - a.z) * (b.x - a.x)) / (b.z - a.z)) {
      result = !result;
    }
  });
  return result;
}

function lambert(color) {
  return new THREE.MeshLambertMaterial({ color, flatShading: true });
}

function mesh(geometry, material, x, y, z) {
  const result = new THREE.Mesh(geometry, material);
  result.position.set(x, y, z);
  return result;
}

function box(w, h, d, material, x, y, z) {
  return mesh(new THREE.BoxGeometry(w, h, d), material, x, y, z);
}

/** A straight strut from ``from`` to ``to``. */
function beam(from, to, thickness, material) {
  const direction = to.clone().sub(from);
  const result = new THREE.Mesh(new THREE.BoxGeometry(thickness, direction.length(), thickness), material);
  result.position.copy(from).addScaledVector(direction, 0.5);
  result.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize());
  return result;
}
