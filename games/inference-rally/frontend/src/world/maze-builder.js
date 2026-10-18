/**
 * Builds the 3D hedge maze from the server's maze layout.
 *
 * The maze is rasterized onto a tile grid at twice the junction resolution: even/even
 * tiles are junctions, tiles between two junctions are roads (if connected) or hedge.
 * Everything repeated (roads, hedges, curbs, trees) uses InstancedMesh, so the whole
 * maze is a handful of draw calls — important with four viewports per frame.
 */

import * as THREE from 'three';
import { LAYERS, TILE, WORLD } from '../config.js';
import { junctionToWorld, tileToWorld } from '../geometry.js';
import { buildCarModel } from '../cars/car-model.js';
import {
  checkerTexture,
  curbTexture,
  flameTexture,
  hedgeTexture,
  hedgeTopTexture,
  junctionTexture,
  roadTexture,
  textTexture,
} from './textures.js';

const START_STRAIGHT_TILES = 2;

const NEIGHBORS = [
  [1, 0],
  [-1, 0],
  [0, 1],
  [0, -1],
];

/**
 * Create the maze group.
 * @param {object} maze Maze view from the ``race_init`` event.
 * @returns {{group: THREE.Group, update: (time: number) => void, exitPosition: THREE.Vector3,
 *            isOpen: (x: number, z: number) => boolean}}
 */
export function buildMaze(maze) {
  const group = new THREE.Group();
  group.name = 'maze';
  const tiles = rasterize(maze);

  group.add(buildFloor(tiles.roads, tiles.junctions));
  group.add(buildHedges(tiles.walls));
  group.add(buildCurbs(tiles));
  group.add(buildTrees(tiles.walls));
  group.add(buildStartLine(maze));

  const finish = buildFinishGate(maze);
  group.add(finish.group);

  const wrecks = maze.dead_ends.map((node) => buildWreck(node, tiles));
  wrecks.forEach((wreck) => group.add(wreck.group));

  group.add(buildOptimalPath(maze.optimal_path));

  return {
    group,
    exitPosition: junctionToWorld(maze.exit),
    /** True if world point (x, z) is on a road or junction (not inside a hedge). */
    isOpen(x, z) {
      return tiles.open.has(`${Math.round(x / TILE)},${Math.round(-z / TILE)}`);
    },
    update(time) {
      finish.update(time);
      wrecks.forEach((wreck) => wreck.update(time));
    },
  };
}

// --------------------------------------------------------------------------- tile grid

function rasterize(maze) {
  const ring = WORLD.outerRing;
  const maxI = 2 * (maze.width - 1);
  const maxJ = 2 * (maze.height - 1);
  const open = new Map(); // "i,j" -> "junction" | "ns" | "ew"

  for (let x = 0; x < maze.width; x += 1) {
    for (let y = 0; y < maze.height; y += 1) open.set(`${2 * x},${2 * y}`, 'junction');
  }
  for (const [[ax, ay], [bx, by]] of maze.roads) {
    open.set(`${ax + bx},${ay + by}`, ax === bx ? 'ns' : 'ew');
  }
  // Starting straight behind the start line (outside the maze, purely visual): gives
  // the chase cameras room on the grid instead of pressing them against a hedge.
  const first = maze.optimal_path[1] ?? [maze.start[0], maze.start[1] + 1];
  const [bi, bj] = [maze.start[0] - first[0], maze.start[1] - first[1]];
  for (let k = 1; k <= START_STRAIGHT_TILES; k += 1) {
    open.set(`${2 * maze.start[0] + bi * k},${2 * maze.start[1] + bj * k}`, bi === 0 ? 'ns' : 'ew');
  }

  const junctions = [];
  const roads = [];
  const walls = [];
  for (let i = -ring; i <= maxI + ring; i += 1) {
    for (let j = -ring; j <= maxJ + ring; j += 1) {
      const kind = open.get(`${i},${j}`);
      if (kind === 'junction') junctions.push([i, j]);
      else if (kind) roads.push([i, j, kind]);
      else walls.push([i, j]);
    }
  }
  return { open, junctions, roads, walls };
}

// --------------------------------------------------------------------------- floor

function buildFloor(roads, junctions) {
  const group = new THREE.Group();
  const plane = new THREE.PlaneGeometry(TILE, TILE).rotateX(-Math.PI / 2);
  const matrix = new THREE.Matrix4();
  const rotation = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  const scale = new THREE.Vector3(1, 1, 1);

  // Each InstancedMesh gets its own geometry: sharing one between instanced meshes can
  // leave stale instance attributes bound when rendering several viewports per frame.
  const roadMesh = new THREE.InstancedMesh(
    plane.clone(),
    new THREE.MeshLambertMaterial({ map: roadTexture() }),
    roads.length,
  );
  roads.forEach(([i, j, kind], index) => {
    // Rotating east-west tiles also rotates their UVs, so the center dashes follow the road.
    rotation.setFromAxisAngle(up, kind === 'ew' ? Math.PI / 2 : 0);
    matrix.compose(tileToWorld(i, j).setY(0.01), rotation, scale);
    roadMesh.setMatrixAt(index, matrix);
  });

  const junctionMesh = new THREE.InstancedMesh(
    plane.clone(),
    new THREE.MeshLambertMaterial({ map: junctionTexture() }),
    junctions.length,
  );
  junctions.forEach(([i, j], index) => {
    matrix.makeTranslation(tileToWorld(i, j).setY(0.012));
    junctionMesh.setMatrixAt(index, matrix);
  });

  for (const mesh of [roadMesh, junctionMesh]) {
    mesh.receiveShadow = true;
    group.add(mesh);
  }
  return group;
}

// --------------------------------------------------------------------------- hedges & curbs

function buildHedges(walls) {
  const side = new THREE.MeshLambertMaterial({ map: hedgeTexture() });
  const top = new THREE.MeshLambertMaterial({ map: hedgeTopTexture() });
  const geometry = new THREE.BoxGeometry(TILE, WORLD.hedgeHeight, TILE);
  // BoxGeometry face order: +x, -x, +y, -y, +z, -z.
  const mesh = new THREE.InstancedMesh(geometry, [side, side, top, top, side, side], walls.length);
  const matrix = new THREE.Matrix4();
  const position = new THREE.Vector3();
  const scale = new THREE.Vector3();
  const identity = new THREE.Quaternion();

  walls.forEach(([i, j], index) => {
    // Slight height variation keeps the hedge mass from looking like a spreadsheet.
    const wobble = 0.9 + hash(i, j) * 0.22;
    scale.set(1, wobble, 1);
    position.copy(tileToWorld(i, j)).setY((WORLD.hedgeHeight * wobble) / 2);
    matrix.compose(position, identity, scale);
    mesh.setMatrixAt(index, matrix);
  });
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function buildCurbs(tiles) {
  const edges = [];
  const openTiles = [...tiles.junctions.map(([i, j]) => [i, j]), ...tiles.roads.map(([i, j]) => [i, j])];
  for (const [i, j] of openTiles) {
    for (const [di, dj] of NEIGHBORS) {
      if (!tiles.open.has(`${i + di},${j + dj}`)) edges.push([i, j, di, dj]);
    }
  }

  const geometry = new THREE.BoxGeometry(TILE, WORLD.curbHeight, WORLD.curbWidth);
  const mesh = new THREE.InstancedMesh(
    geometry,
    new THREE.MeshLambertMaterial({ map: curbTexture() }),
    edges.length,
  );
  const matrix = new THREE.Matrix4();
  const rotation = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  const scale = new THREE.Vector3(1, 1, 1);
  const inset = TILE / 2 - WORLD.curbWidth / 2;

  edges.forEach(([i, j, di, dj], index) => {
    const position = tileToWorld(i, j);
    position.x += di * inset;
    position.z -= dj * inset; // grid north (+j) is world -Z
    position.y = WORLD.curbHeight / 2;
    rotation.setFromAxisAngle(up, di !== 0 ? Math.PI / 2 : 0);
    matrix.compose(position, rotation, scale);
    mesh.setMatrixAt(index, matrix);
  });
  mesh.receiveShadow = true;
  return mesh;
}

// --------------------------------------------------------------------------- trees

function buildTrees(walls) {
  const spots = walls.filter(([i, j]) => hash(i * 3, j * 7) < WORLD.treeChance);
  const group = new THREE.Group();
  const trunk = new THREE.InstancedMesh(
    new THREE.CylinderGeometry(0.35, 0.5, 3.2, 5),
    new THREE.MeshLambertMaterial({ color: 0x5a3d22, flatShading: true }),
    spots.length,
  );
  const lower = new THREE.InstancedMesh(
    new THREE.ConeGeometry(3.0, 5.2, 7),
    new THREE.MeshLambertMaterial({ color: 0xffffff, flatShading: true }),
    spots.length,
  );
  const upper = new THREE.InstancedMesh(
    new THREE.ConeGeometry(2.1, 4.0, 7),
    new THREE.MeshLambertMaterial({ color: 0xffffff, flatShading: true }),
    spots.length,
  );
  const matrix = new THREE.Matrix4();
  const color = new THREE.Color();
  const greens = [0x2b5e33, 0x356b3b, 0x24522c, 0x3d7a42];

  spots.forEach(([i, j], index) => {
    const base = tileToWorld(i, j);
    const jitterX = (hash(i, j * 5) - 0.5) * 4;
    const jitterZ = (hash(i * 5, j) - 0.5) * 4;
    const size = 0.85 + hash(i * 11, j * 13) * 0.5;
    const y = WORLD.hedgeHeight;
    matrix.makeScale(size, size, size).setPosition(base.x + jitterX, y + 1.4 * size, base.z + jitterZ);
    trunk.setMatrixAt(index, matrix);
    matrix.makeScale(size, size, size).setPosition(base.x + jitterX, y + 4.4 * size, base.z + jitterZ);
    lower.setMatrixAt(index, matrix);
    matrix.makeScale(size, size, size).setPosition(base.x + jitterX, y + 6.8 * size, base.z + jitterZ);
    upper.setMatrixAt(index, matrix);
    color.setHex(greens[Math.floor(hash(i * 17, j * 19) * greens.length)]);
    lower.setColorAt(index, color);
    upper.setColorAt(index, color.offsetHSL(0, 0, 0.04));
  });
  for (const mesh of [trunk, lower, upper]) {
    mesh.castShadow = true;
    group.add(mesh);
  }
  return group;
}

// --------------------------------------------------------------------------- landmarks

function buildStartLine(maze) {
  const first = maze.optimal_path[1] ?? maze.start;
  const horizontal = first[1] === maze.start[1]; // first road runs east-west
  const line = new THREE.Mesh(
    new THREE.PlaneGeometry(TILE, 1.6).rotateX(-Math.PI / 2),
    new THREE.MeshLambertMaterial({ map: checkerTexture(10, 2) }),
  );
  line.position.copy(junctionToWorld(maze.start, 0.03));
  if (horizontal) line.rotation.y = Math.PI / 2;
  line.receiveShadow = true;
  return line;
}

function buildFinishGate(maze) {
  const group = new THREE.Group();
  const path = maze.optimal_path;
  const before = path[path.length - 2] ?? maze.start;
  const dx = maze.exit[0] - before[0];
  // Gate faces the last road of the optimal route.
  group.rotation.y = dx !== 0 ? Math.PI / 2 : 0;
  group.position.copy(junctionToWorld(maze.exit));

  const postMaterial = new THREE.MeshLambertMaterial({ color: 0xeeeeee, flatShading: true });
  for (const side of [-1, 1]) {
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.8, 8, 0.8), postMaterial);
    post.position.set(side * (TILE / 2 - 0.6), 4, 0);
    post.castShadow = true;
    group.add(post);
  }
  const banner = new THREE.Mesh(
    new THREE.BoxGeometry(TILE, 1.8, 0.4),
    [
      postMaterial,
      postMaterial,
      new THREE.MeshLambertMaterial({ map: checkerTexture(12, 1) }),
      postMaterial,
      new THREE.MeshBasicMaterial({ map: textTexture('FINISH', { color: '#ffd23f', background: '#d63a2f' }) }),
      new THREE.MeshBasicMaterial({ map: textTexture('FINISH', { color: '#ffd23f', background: '#d63a2f' }) }),
    ],
  );
  banner.position.y = 7.6;
  banner.castShadow = true;
  group.add(banner);

  // A tall translucent beam shows where the exit is from anywhere in the maze.
  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(2.2, 2.2, 90, 10, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0x8ef07a,
      transparent: true,
      opacity: 0.22,
      depthWrite: false,
      side: THREE.DoubleSide,
      fog: false,
    }),
  );
  beam.position.y = 45;
  group.add(beam);

  const pad = new THREE.Mesh(
    new THREE.PlaneGeometry(TILE * 0.9, TILE * 0.9).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: 0x8ef07a, transparent: true, opacity: 0.35 }),
  );
  pad.position.y = 0.05;
  group.add(pad);

  return {
    group,
    update(time) {
      beam.material.opacity = 0.16 + Math.sin(time * 3) * 0.06;
      pad.material.opacity = 0.25 + Math.sin(time * 4) * 0.1;
    },
  };
}

/** A burned-out car with pixel flames marks every dead end. */
function buildWreck(node, tiles) {
  const group = new THREE.Group();
  const center = junctionToWorld(node);
  const [i, j] = [node[0] * 2, node[1] * 2];
  // Find the single open side; the wreck sits against the opposite (closed) end.
  const [di, dj] = NEIGHBORS.find(([a, b]) => tiles.open.has(`${i + a},${j + b}`)) ?? [0, 1];
  const away = new THREE.Vector3(-di, 0, dj);
  group.position.copy(center).addScaledVector(away, TILE / 2 - 2.4);
  group.rotation.y = Math.atan2(-away.x, -away.z) + 0.5;

  const wreck = buildCarModel({ color: 0xbdbdbd, plate: 'X', lights: false });
  wreck.rotation.z = 0.12;
  group.add(wreck);

  const flames = [];
  for (let k = 0; k < 4; k += 1) {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: flameTexture(10 + k), transparent: true, depthWrite: false }),
    );
    sprite.position.set((k - 1.5) * 0.7, 1.8, (k % 2) * 0.8 - 0.4);
    sprite.userData.phase = k * 1.7;
    group.add(sprite);
    flames.push(sprite);
  }
  return {
    group,
    update(time) {
      for (const flame of flames) {
        const flicker = Math.sin(time * 14 + flame.userData.phase) * 0.25 + 1;
        flame.scale.set(1.4 * flicker, 2.4 * (2 - flicker), 1);
        flame.material.opacity = 0.75 + Math.sin(time * 21 + flame.userData.phase) * 0.25;
      }
    },
  };
}

/** Dashed A* route, visible only in the overview map. */
function buildOptimalPath(path) {
  const group = new THREE.Group();
  const material = new THREE.MeshBasicMaterial({ color: 0xf4f4f4, transparent: true, opacity: 0.85 });
  const dash = new THREE.BoxGeometry(1.2, 0.2, 3.2);
  for (let k = 0; k < path.length - 1; k += 1) {
    const a = junctionToWorld(path[k], 0.5);
    const b = junctionToWorld(path[k + 1], 0.5);
    for (const t of [0.2, 0.5, 0.8]) {
      const piece = new THREE.Mesh(dash, material);
      piece.position.lerpVectors(a, b, t);
      piece.rotation.y = a.x === b.x ? 0 : Math.PI / 2;
      piece.layers.set(LAYERS.overview);
      group.add(piece);
    }
  }
  return group;
}

/** Stable pseudo-random value in [0, 1) for a tile, so decoration never changes per load. */
function hash(i, j) {
  const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

