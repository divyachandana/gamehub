/**
 * Latitude/longitude to world positions around the raced area.
 *
 * Uses the same flat projection as the server's ``city_map.Frame``. Inside the street grid,
 * real meters map piecewise onto the junctions, so a landmark on a real block lands on that
 * block; beyond the grid, distances shrink smoothly so far landmarks still fit in the fog.
 */

import * as THREE from 'three';
import { GEO } from '../config.js';
import { junctionToWorld, worldToJunction } from '../geometry.js';

const METERS_PER_DEGREE_LAT = 110574;
const METERS_PER_DEGREE_LON = 111320;

export class GeoFrame {
  /**
   * @param {object|null} city Street map from the server (``maze.city``); null for a random grid.
   * @param {{width: number, height: number}} maze
   */
  constructor(city, maze, { squeeze = true } = {}) {
    this.squeeze = squeeze; // false: real distances everywhere (the map view's flat map)
    [this.lat0, this.lon0] = city?.origin ?? GEO.fallbackOrigin;
    this.rotation = THREE.MathUtils.degToRad(city?.rotation_deg ?? GEO.fallbackRotationDeg);
    this.xs = city ? city.columns.map((line) => line.offset_m) : spaced(maze.width, GEO.fallbackSpacingM[0]);
    this.ys = city ? city.rows.map((line) => line.offset_m) : spaced(maze.height, GEO.fallbackSpacingM[1]);
  }

  /**
   * World position of a latitude/longitude.
   * @param {number} lat
   * @param {number} lon
   * @param {number} [y=0]
   * @returns {THREE.Vector3}
   */
  toWorld(lat, lon, y = 0) {
    const east = (lon - this.lon0) * METERS_PER_DEGREE_LON * Math.cos(THREE.MathUtils.degToRad(this.lat0));
    const north = (lat - this.lat0) * METERS_PER_DEGREE_LAT;
    const [cos, sin] = [Math.cos(this.rotation), Math.sin(this.rotation)];
    const column = toGrid(east * cos + north * sin, this.xs, this.squeeze);
    const row = toGrid(north * cos - east * sin, this.ys, this.squeeze);
    return junctionToWorld([column, row], y);
  }

  /**
   * Latitude/longitude of a world position in or near the grid (the inverse of ``toWorld``).
   * @param {number} x World X.
   * @param {number} z World Z.
   * @returns {[number, number]}
   */
  toLatLon(x, z) {
    const [column, row] = worldToJunction(x, z);
    const across = fromGrid(column, this.xs);
    const along = fromGrid(row, this.ys);
    const [cos, sin] = [Math.cos(this.rotation), Math.sin(this.rotation)];
    const east = across * cos - along * sin;
    const north = across * sin + along * cos;
    return [
      this.lat0 + north / METERS_PER_DEGREE_LAT,
      this.lon0 + east / (METERS_PER_DEGREE_LON * Math.cos(THREE.MathUtils.degToRad(this.lat0))),
    ];
  }
}

/**
 * World height of a real building, squeezed so towers do not dwarf the race.
 * @param {number} meters
 */
export const realHeight = (meters) => GEO.heightScale * meters ** GEO.heightPower;

/**
 * World size of a real footprint side, squeezed like heights.
 * @param {number} meters
 */
export const realFootprint = (meters) => GEO.footprintScale * meters ** GEO.footprintPower;

function spaced(count, spacing) {
  return Array.from({ length: count }, (_, k) => (k - (count - 1) / 2) * spacing);
}

/** Fractional junction index of a position in meters along one grid axis. */
function toGrid(meters, lines, squeezed) {
  const last = lines.length - 1;
  const outside = squeezed ? squeeze : (distance, blockMeters) => distance / blockMeters;
  if (meters < lines[0]) return -outside(lines[0] - meters, lines[1] - lines[0]);
  if (meters > lines[last]) return last + outside(meters - lines[last], lines[last] - lines[last - 1]);
  let k = 0;
  while (k < last - 1 && meters > lines[k + 1]) k += 1;
  return k + (meters - lines[k]) / (lines[k + 1] - lines[k]);
}

/** Meters along one grid axis of a fractional junction index; straight beyond the ends. */
function fromGrid(index, lines) {
  const last = lines.length - 1;
  const k = THREE.MathUtils.clamp(Math.floor(index), 0, last - 1);
  return lines[k] + (index - k) * (lines[k + 1] - lines[k]);
}

/** Blocks beyond the grid edge: one block per real block at first, flattening with distance. */
function squeeze(meters, blockMeters) {
  return (GEO.squeezeM / blockMeters) * (1 - Math.exp(-meters / GEO.squeezeM));
}
