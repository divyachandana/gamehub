/**
 * Builds the 3D city maze (San Francisco streets) from the server's maze layout.
 *
 * The maze is rasterized onto a tile grid at twice the junction resolution: even/even
 * tiles are junctions, tiles between two junctions are roads (if connected) or a city
 * block. Everything repeated (roads, buildings, sidewalks) uses InstancedMesh, so the
 * whole maze is a few dozen draw calls — important with four viewports per frame.
 */

import * as THREE from 'three';
import { LAYERS, TILE, WORLD } from '../config.js';
import { junctionToWorld, tileToWorld } from '../geometry.js';
import { buildCarModel } from '../cars/car-model.js';
import { buildCityBlocks } from './city-blocks.js';
import {
  barricadeTexture,
  checkerTexture,
  flameTexture,
  junctionTexture,
  roadTexture,
  sidewalkTexture,
  textTexture,
} from './textures.js';

const START_STRAIGHT_TILES = 2;
const BARRICADE_INSET = 1.4; // from the junction end of a closed street

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

  group.add(buildFloor([...tiles.roads, ...tiles.closed], tiles.junctions));
  group.add(buildCityBlocks(tiles.walls, tiles.open, maze));
  group.add(buildSidewalks(tiles));
  if (tiles.closed.length) group.add(buildBarricades(closedTilePlacements(tiles.closed)));
  group.add(buildStartLine(maze));

  const finish = buildFinishGate(maze);
  group.add(finish.group);

  const wrecks = maze.dead_ends.map((node) => buildWreck(node, tiles));
  wrecks.forEach((wreck) => group.add(wreck.group));

  group.add(buildOptimalPath(maze.optimal_path));

  return {
    group,
    exitPosition: junctionToWorld(maze.exit),
    /** True if world point (x, z) is on a road or junction (not inside a building). */
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

  // A junction no road reaches (a real map's gap, e.g. a park) stays a city block.
  for (const [a, b] of maze.roads) {
    for (const [x, y] of [a, b]) open.set(`${2 * x},${2 * y}`, 'junction');
  }
  for (const [[ax, ay], [bx, by]] of maze.roads) {
    open.set(`${ax + bx},${ay + by}`, ax === bx ? 'ns' : 'ew');
  }
  // Real streets this race closed: drawn as streets, but walls to cars and cameras.
  const closed = (maze.closed ?? []).map(([[ax, ay], [bx, by]]) => [ax + bx, ay + by, ax === bx ? 'ns' : 'ew']);
  const closedKeys = new Set(closed.map(([i, j]) => `${i},${j}`));
  // Starting straight behind the start line (outside the maze, purely visual): gives
  // the chase cameras room on the grid instead of pressing them against a building.
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
      else if (!closedKeys.has(`${i},${j}`)) walls.push([i, j]);
    }
  }
  return { open, junctions, roads, walls, closed, closedKeys };
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

// --------------------------------------------------------------------------- sidewalks

/** Sidewalks along every edge between a street and a building; red curbs at corners. */
function buildSidewalks(tiles) {
  const group = new THREE.Group();
  const street = (i, j) => tiles.open.has(`${i},${j}`) || tiles.closedKeys.has(`${i},${j}`);
  for (const [streetTiles, redCurb] of [
    [[...tiles.roads, ...tiles.closed], false],
    [tiles.junctions, true],
  ]) {
    const edges = [];
    for (const [i, j] of streetTiles) {
      for (const [di, dj] of NEIGHBORS) {
        if (!street(i + di, j + dj)) edges.push([i, j, di, dj]);
      }
    }
    group.add(sidewalkMesh(edges, redCurb));
  }
  return group;
}

/** A barricade near both ends of every closed street tile. */
function closedTilePlacements(closed) {
  return closed.flatMap(([i, j, kind]) =>
    [-1, 1].map((end) => {
      const along = TILE / 2 - BARRICADE_INSET;
      const offset = kind === 'ew' ? new THREE.Vector3(end * along, 0, 0) : new THREE.Vector3(0, 0, end * along);
      return { position: tileToWorld(i, j).add(offset), kind };
    }),
  );
}

/**
 * Striped barricades with a ROAD CLOSED sign, each across its street.
 * @param {{position: THREE.Vector3, kind: 'ns'|'ew'}[]} placements Street direction per barricade.
 * @returns {THREE.Group}
 */
export function buildBarricades(placements) {
  const count = placements.length;
  const boards = new THREE.InstancedMesh(
    new THREE.BoxGeometry(TILE - 2 * WORLD.sidewalkWidth, 0.4, 0.2),
    new THREE.MeshLambertMaterial({ map: barricadeTexture() }),
    count * 2,
  );
  const legs = new THREE.InstancedMesh(
    new THREE.BoxGeometry(0.2, 1.8, 0.7),
    new THREE.MeshLambertMaterial({ color: 0xf0782a }),
    count * 2,
  );
  const signFace = new THREE.MeshBasicMaterial({
    map: textTexture('ROAD CLOSED', { width: 96, height: 20, color: '#111111', background: '#f2a33a' }),
  });
  const signEdge = new THREE.MeshLambertMaterial({ color: 0xf2a33a });
  const signs = new THREE.InstancedMesh(
    new THREE.BoxGeometry(3.4, 0.8, 0.12),
    [signEdge, signEdge, signEdge, signEdge, signFace, signFace],
    count,
  );
  const matrix = new THREE.Matrix4();
  const part = new THREE.Matrix4();
  const up = new THREE.Vector3(0, 1, 0);
  const halfRoad = TILE / 2 - WORLD.sidewalkWidth;

  placements.forEach(({ position, kind }, index) => {
    // Barricades are built across X; a north-south street turns nothing, east-west turns 90°.
    matrix.makeRotationAxis(up, kind === 'ew' ? Math.PI / 2 : 0).setPosition(position);
    for (const [n, y] of [
      [0, 0.8],
      [1, 1.4],
    ]) {
      boards.setMatrixAt(index * 2 + n, part.makeTranslation(0, y, 0).premultiply(matrix));
    }
    for (const [n, side] of [
      [0, -1],
      [1, 1],
    ]) {
      legs.setMatrixAt(index * 2 + n, part.makeTranslation(side * (halfRoad - 0.4), 0.9, 0).premultiply(matrix));
    }
    signs.setMatrixAt(index, part.makeTranslation(0, 2.3, 0).premultiply(matrix));
  });
  const group = new THREE.Group();
  for (const mesh of [boards, legs, signs]) {
    mesh.castShadow = true;
    group.add(mesh);
  }
  return group;
}

function sidewalkMesh(edges, redCurb) {
  const geometry = new THREE.BoxGeometry(TILE, WORLD.sidewalkHeight, WORLD.sidewalkWidth);
  const mesh = new THREE.InstancedMesh(
    geometry,
    new THREE.MeshLambertMaterial({ map: sidewalkTexture(redCurb) }),
    edges.length,
  );
  const matrix = new THREE.Matrix4();
  const rotation = new THREE.Quaternion();
  const up = new THREE.Vector3(0, 1, 0);
  const scale = new THREE.Vector3(1, 1, 1);
  const inset = TILE / 2 - WORLD.sidewalkWidth / 2;

  edges.forEach(([i, j, di, dj], index) => {
    const position = tileToWorld(i, j);
    position.x += di * inset;
    position.z -= dj * inset; // grid north (+j) is world -Z
    // Slabs overlap at inside corners: lift one direction a hair so their tops never z-fight.
    position.y = WORLD.sidewalkHeight / 2 + (di !== 0 ? 0.01 : 0);
    rotation.setFromAxisAngle(up, di !== 0 ? Math.PI / 2 : 0);
    matrix.compose(position, rotation, scale);
    mesh.setMatrixAt(index, matrix);
  });
  mesh.receiveShadow = true;
  return mesh;
}

// --------------------------------------------------------------------------- landmarks

/** Checkered line across the start junction. @param {object} maze */
export function buildStartLine(maze) {
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

/** FINISH banner, green beam and pad at the exit. @param {object} maze */
export function buildFinishGate(maze) {
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
