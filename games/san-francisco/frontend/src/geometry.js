/**
 * Conversions between the server's grid coordinates and the 3D world.
 *
 * Grid: (x, y) with x east and y north. World: X east, Z south (three.js convention),
 * so north is -Z. Cars are modelled facing -Z (north) at rotation.y = 0.
 */

import * as THREE from 'three';
import { BLOCK, TILE } from './config.js';

const HEADING_ANGLE = { N: 0, W: Math.PI / 2, S: Math.PI, E: -Math.PI / 2 };
// World X of each junction column and world -Z of each row, set for a real street map
// (real distances); null means the even grid, one BLOCK apart.
let columnLines = null;
let rowLines = null;
const HEADING_ORDER = ['N', 'E', 'S', 'W'];
const TURN_OFFSET = { straight: 0, right: 1, back: 2, left: 3 };

/**
 * Space junctions as far apart as the real streets (``null`` restores the even grid).
 * @param {number[]|null} columns World X of each junction column, west to east.
 * @param {number[]|null} rows World distance north of each junction row, south to north.
 */
export function setJunctionLines(columns, rows) {
  columnLines = columns;
  rowLines = rows;
}

/**
 * World position of a junction's center; fractional junctions lie on the streets between.
 * @param {[number, number]} node Grid junction (x, y).
 * @param {number} [y=0] Height above the ground.
 * @returns {THREE.Vector3}
 */
export function junctionToWorld([x, y], height = 0) {
  return new THREE.Vector3(alongLines(x, columnLines), height, -alongLines(y, rowLines));
}

/**
 * Fractional junction (x, y) of a world position: the inverse of ``junctionToWorld``.
 * @param {number} worldX
 * @param {number} worldZ
 * @returns {[number, number]}
 */
export function worldToJunction(worldX, worldZ) {
  return [linesIndex(worldX, columnLines), linesIndex(-worldZ, rowLines)];
}

/** Position of a fractional junction index along one axis; straight beyond the ends. */
function alongLines(index, lines) {
  if (!lines) return index * BLOCK;
  const k = THREE.MathUtils.clamp(Math.floor(index), 0, lines.length - 2);
  return lines[k] + (index - k) * (lines[k + 1] - lines[k]);
}

function linesIndex(position, lines) {
  if (!lines) return position / BLOCK;
  let k = 0;
  while (k < lines.length - 2 && position > lines[k + 1]) k += 1;
  return k + (position - lines[k]) / (lines[k + 1] - lines[k]);
}

/**
 * World position of a tile's center in the 2x-resolution tile grid
 * (even/even tiles are junctions, odd tiles are roads or walls).
 * @param {number} i Tile column.
 * @param {number} j Tile row.
 * @returns {THREE.Vector3}
 */
export function tileToWorld(i, j) {
  return new THREE.Vector3(i * TILE, 0, -j * TILE);
}

/**
 * Yaw angle (rotation.y) for a compass heading.
 * @param {'N'|'E'|'S'|'W'} heading
 * @returns {number}
 */
export function headingToAngle(heading) {
  return HEADING_ANGLE[heading];
}

/**
 * Absolute heading after a relative move.
 * @param {'N'|'E'|'S'|'W'} heading Current heading.
 * @param {'left'|'straight'|'right'|'back'} move Relative move.
 * @returns {'N'|'E'|'S'|'W'}
 */
export function turnHeading(heading, move) {
  const index = HEADING_ORDER.indexOf(heading);
  return HEADING_ORDER[(index + TURN_OFFSET[move]) % 4];
}

/**
 * Unit vector in world space for a compass heading.
 * @param {'N'|'E'|'S'|'W'} heading
 * @returns {THREE.Vector3}
 */
export function headingToVector(heading) {
  const angle = headingToAngle(heading);
  return new THREE.Vector3(-Math.sin(angle), 0, -Math.cos(angle));
}

/**
 * Shortest signed difference between two angles, in (-PI, PI].
 * @param {number} from
 * @param {number} to
 * @returns {number}
 */
export function angleDelta(from, to) {
  let delta = (to - from) % (Math.PI * 2);
  if (delta > Math.PI) delta -= Math.PI * 2;
  if (delta <= -Math.PI) delta += Math.PI * 2;
  return delta;
}

/** @param {number} t 0..1 */
export const easeInOutQuad = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);

/** @param {number} t 0..1 */
export const easeOutCubic = (t) => 1 - (1 - t) ** 3;
