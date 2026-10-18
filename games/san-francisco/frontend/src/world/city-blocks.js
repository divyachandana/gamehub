/**
 * The maze walls as San Francisco city blocks. With a real street map each block is built
 * from its real buildings' typical height: painted-lady row houses with gable roofs, flats,
 * office towers, or a park. A random grid falls back to houses that turn into flats toward
 * the exit. One InstancedMesh per look keeps the whole city to a few dozen draw calls.
 */

import * as THREE from 'three';
import { COLORS, TILE, WORLD } from '../config.js';
import { tileToWorld } from '../geometry.js';
import { realHeight } from './geo.js';
import { facadeTexture, flatRoofTexture, grassTexture, repeatEvery4, towerTexture } from './textures.js';

const FLOOR_HEIGHT = 4;
const HOUSE_FLOORS = 2;
const HOUSE_MAX_M = 11; // real blocks up to this height are row houses
const TOWER_FLOORS = 7; // from this many floors a block is an office tower
const MAX_FLOORS = 14;
const PARK_SHARE = 0.5;
const PARK_TREES = 4;

/**
 * Create every building and park of the maze.
 * @param {[number, number][]} walls Wall tiles (i, j).
 * @param {Map<string, string>} open Open tiles keyed "i,j" (to turn gables to the street).
 * @param {object} maze Maze view; ``maze.city`` holds the real blocks, if any.
 * @returns {THREE.Group}
 */
export function buildCityBlocks(walls, open, maze) {
  const blockAt = blockLookup(maze);
  const lots = [];
  for (const [i, j] of walls) {
    const block = blockAt(i, j);
    const look = block ? realLook(block) : fallbackLook(i, j, maze);
    const paints = { house: COLORS.housePaints, flats: COLORS.flatsPaints, tower: COLORS.towers }[look.kind];
    lots.push({
      i,
      j,
      ...look,
      // Small height differences keep a street of equal houses from looking like a spreadsheet.
      scale: 0.94 + hash(i * 5, j * 11) * 0.12,
      paint: paints ? Math.floor(hash(i * 13, j * 17) * paints.length) : 0,
    });
  }

  const group = new THREE.Group();
  const houses = lots.filter((lot) => lot.kind === 'house');
  group.add(...buildBuildings(lots.filter((lot) => lot.kind !== 'park')), ...buildGableRoofs(houses, open));
  const parks = lots.filter((lot) => lot.kind === 'park');
  if (parks.length) group.add(buildParks(parks));
  return group;
}

/** Look of a real block: park, or buildings as tall as its real ones. */
function realLook(block) {
  if (block.park >= PARK_SHARE) return { kind: 'park' };
  if (block.height_m <= HOUSE_MAX_M) return { kind: 'house', floors: HOUSE_FLOORS };
  const floors = THREE.MathUtils.clamp(Math.round(realHeight(block.height_m) / FLOOR_HEIGHT), 3, MAX_FLOORS);
  return { kind: floors >= TOWER_FLOORS ? 'tower' : 'flats', floors };
}

/** Random grid: houses near the start, more flats toward the exit (downtown). */
function fallbackLook(i, j, maze) {
  const diagonal = 2 * (maze.width - 1) + 2 * (maze.height - 1);
  const downtown = THREE.MathUtils.clamp((i + j) / diagonal, 0, 1);
  const flatsShare = THREE.MathUtils.lerp(WORLD.flatsNearStart, WORLD.flatsNearExit, downtown);
  return hash(i * 7, j * 3) < flatsShare ? { kind: 'flats', floors: 3 } : { kind: 'house', floors: HOUSE_FLOORS };
}

/** Tile (i, j) to the real block it stands on; tiles beyond the data use the nearest block. */
function blockLookup(maze) {
  if (!maze.city) return () => null;
  const blocks = new Map(maze.city.blocks.map((block) => [`${block.x},${block.y}`, block]));
  return (i, j) => {
    const x = THREE.MathUtils.clamp(Math.floor((i - 1) / 2), -1, maze.width - 1);
    const y = THREE.MathUtils.clamp(Math.floor((j - 1) / 2), -1, maze.height - 1);
    return blocks.get(`${x},${y}`) ?? null;
  };
}

// --------------------------------------------------------------------------- buildings

/**
 * Box with two material groups, walls then roof and floor. Each group is one draw call per
 * viewport, so this needs a third of the calls of a plain six-group BoxGeometry.
 */
function wallsAndRoofBox(width, height, depth) {
  const geometry = new THREE.BoxGeometry(width, height, depth);
  const index = geometry.index.array;
  // BoxGeometry face order: +x, -x, +y, -y, +z, -z; six indices per face.
  const faces = (ids) => ids.flatMap((face) => Array.from(index.slice(face * 6, face * 6 + 6)));
  geometry.setIndex([...faces([0, 1, 4, 5]), ...faces([2, 3])]);
  geometry.clearGroups();
  geometry.addGroup(0, 24, 0);
  geometry.addGroup(24, 12, 1);
  return geometry;
}

function buildBuildings(lots) {
  const roof = new THREE.MeshLambertMaterial({ map: flatRoofTexture() });
  const groups = new Map();
  for (const lot of lots) {
    const key = `${lot.kind}:${lot.floors}:${lot.paint}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(lot);
  }
  const matrix = new THREE.Matrix4();
  const position = new THREE.Vector3();
  const scale = new THREE.Vector3();
  const identity = new THREE.Quaternion();

  return [...groups.values()].map((own) => {
    const { kind, floors, paint } = own[0];
    const height = floors * FLOOR_HEIGHT;
    const geometry = wallsAndRoofBox(TILE, height, TILE);
    let side;
    if (kind === 'tower') {
      repeatEvery4(geometry, TILE, height);
      side = new THREE.MeshLambertMaterial({ map: towerTexture(COLORS.towers[paint]) });
    } else {
      const paints = kind === 'house' ? COLORS.housePaints : COLORS.flatsPaints;
      side = new THREE.MeshLambertMaterial({ map: facadeTexture(paints[paint], floors) });
    }
    const mesh = new THREE.InstancedMesh(geometry, [side, roof], own.length);
    own.forEach((lot, k) => {
      scale.set(1, lot.scale, 1);
      position.copy(tileToWorld(lot.i, lot.j)).setY((height * lot.scale) / 2);
      mesh.setMatrixAt(k, matrix.compose(position, identity, scale));
    });
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  });
}

/** Gable roofs: slopes in roof colors, the gable ends in the house's paint, facing a street. */
function buildGableRoofs(houses, open) {
  if (!houses.length) return [];
  const ends = new THREE.InstancedMesh(gableEndsGeometry(), roofMaterial(), houses.length);
  const slopes = new THREE.InstancedMesh(slopesGeometry(), roofMaterial(), houses.length);
  const matrix = new THREE.Matrix4();
  const color = new THREE.Color();
  const up = new THREE.Vector3(0, 1, 0);

  houses.forEach(({ i, j, scale, paint }, k) => {
    const streetNorthSouth = open.has(`${i},${j + 1}`) || open.has(`${i},${j - 1}`);
    const streetEastWest = open.has(`${i + 1},${j}`) || open.has(`${i - 1},${j}`);
    const turn = streetEastWest && (!streetNorthSouth || hash(i * 3, j * 5) < 0.5);
    matrix.makeRotationAxis(up, turn ? Math.PI / 2 : 0);
    matrix.setPosition(tileToWorld(i, j).setY(HOUSE_FLOORS * FLOOR_HEIGHT * scale));
    ends.setMatrixAt(k, matrix);
    slopes.setMatrixAt(k, matrix);
    ends.setColorAt(k, color.set(COLORS.housePaints[paint]));
    slopes.setColorAt(k, color.setHex(COLORS.roofs[Math.floor(hash(i * 19, j * 23) * COLORS.roofs.length)]));
  });
  for (const mesh of [ends, slopes]) {
    mesh.castShadow = true;
    mesh.receiveShadow = true;
  }
  return [ends, slopes];
}

function roofMaterial() {
  return new THREE.MeshLambertMaterial({ color: 0xffffff, flatShading: true });
}

// Roof prism over one tile, ridge along Z at roofHeight; triangles wind outward.
const HALF = TILE / 2;

function gableEndsGeometry() {
  const h = WORLD.roofHeight;
  return trianglesGeometry([
    [[-HALF, 0, HALF], [HALF, 0, HALF], [0, h, HALF]],
    [[HALF, 0, -HALF], [-HALF, 0, -HALF], [0, h, -HALF]],
  ]);
}

function slopesGeometry() {
  const h = WORLD.roofHeight;
  const [a, b, c, d] = [[-HALF, 0, -HALF], [HALF, 0, -HALF], [HALF, 0, HALF], [-HALF, 0, HALF]];
  const [e, f] = [[0, h, -HALF], [0, h, HALF]];
  return trianglesGeometry([
    [a, d, f],
    [a, f, e],
    [c, b, e],
    [c, e, f],
  ]);
}

function trianglesGeometry(triangles) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(triangles.flat(2), 3));
  geometry.computeVertexNormals();
  return geometry;
}

// --------------------------------------------------------------------------- parks

/** Lawn with a low hedge around it and a few round trees. */
function buildParks(parks) {
  const group = new THREE.Group();
  const matrix = new THREE.Matrix4();
  const lawn = new THREE.InstancedMesh(
    new THREE.BoxGeometry(TILE, 0.5, TILE),
    new THREE.MeshLambertMaterial({ map: grassTexture(2) }),
    parks.length,
  );
  const trunks = new THREE.InstancedMesh(
    new THREE.CylinderGeometry(0.3, 0.4, 2.6, 5),
    new THREE.MeshLambertMaterial({ color: 0x5a3d22, flatShading: true }),
    parks.length * PARK_TREES,
  );
  const crowns = new THREE.InstancedMesh(
    new THREE.IcosahedronGeometry(1.9, 0),
    new THREE.MeshLambertMaterial({ color: 0x3d7a42, flatShading: true }),
    parks.length * PARK_TREES,
  );
  parks.forEach(({ i, j }, k) => {
    const base = tileToWorld(i, j);
    lawn.setMatrixAt(k, matrix.makeTranslation(base.x, 0.25, base.z));
    for (let t = 0; t < PARK_TREES; t += 1) {
      const x = base.x + (hash(i * 3 + t, j * 7) - 0.5) * 6.5;
      const z = base.z + (hash(i * 5, j * 3 + t) - 0.5) * 6.5;
      const size = 0.8 + hash(i + t, j - t) * 0.6;
      trunks.setMatrixAt(k * PARK_TREES + t, matrix.makeScale(size, size, size).setPosition(x, 1.3 * size, z));
      crowns.setMatrixAt(k * PARK_TREES + t, matrix.makeScale(size, size, size).setPosition(x, 3.6 * size, z));
    }
  });
  for (const mesh of [lawn, trunks, crowns]) {
    mesh.castShadow = mesh !== lawn;
    mesh.receiveShadow = true;
    group.add(mesh);
  }
  return group;
}

/** Stable pseudo-random value in [0, 1) for a tile, so the city never changes per load. */
function hash(i, j) {
  const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453;
  return s - Math.floor(s);
}
