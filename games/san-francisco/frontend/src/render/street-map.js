/**
 * The overview map as a real street map: OpenStreetMap's map pictures of the raced area
 * (north up, real street names), with the best route, the streets closed for this race,
 * each racer's trail and a dot per car. Used instead of the 3D top-down view whenever the
 * race runs on a real street map; the pictures come from ``scripts/fetch_map.py``.
 */

import { junctionToWorld } from '../geometry.js';

const TILE_PX = 256;
const TILES_URL = '/api/tiles';
const MARGIN_M = 60; // map border around the race area
const EARTH_M = 40075016.686; // Web Mercator spreads the equator over one zoom-0 tile
const TRAIL_SPREAD_PX = 3; // side-by-side trails stay visible where routes overlap
const ROUTE_COLOR = '#1a73e8';
const CLOSED_COLOR = '#d93025';
const BACKGROUND = '#e8e4dc';
const LABEL_FONT = '7px "Press Start 2P", monospace';

export class StreetMap {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'street-map';
    this.ctx = this.canvas.getContext('2d');
    this.tiles = new Map(); // "z/x/y" -> HTMLImageElement
    this.active = false;
  }

  /**
   * Prepare for a race; stays inactive (and detached) when the race has no street map.
   * @param {HTMLElement} pane Overview pane; the map fills its left part.
   * @param {object} maze Maze view from ``race_init``.
   * @param {import('../world/geo.js').GeoFrame} geo
   */
  setup(pane, maze, geo) {
    this.active = Boolean(maze.city?.tile_zooms?.length);
    if (!this.active) {
      this.canvas.remove();
      return;
    }
    pane.prepend(this.canvas);
    this.maze = maze;
    this.geo = geo;
    this.zooms = [...maze.city.tile_zooms].sort((a, b) => a - b);
    this.trails = new Map(); // racer id -> {color, spread, roads: [[src, dst], ...]}
    this.points = new Map(); // "x,y" junction -> Web Mercator point
    for (let x = 0; x < maze.width; x += 1) {
      for (let y = 0; y < maze.height; y += 1) this.points.set(`${x},${y}`, this.mercatorOf(junctionToWorld([x, y])));
    }
    const all = [...this.points.values()];
    const margin = MARGIN_M / (EARTH_M * Math.cos((geo.lat0 * Math.PI) / 180));
    this.bounds = {
      left: Math.min(...all.map((p) => p[0])) - margin,
      right: Math.max(...all.map((p) => p[0])) + margin,
      top: Math.min(...all.map((p) => p[1])) - margin,
      bottom: Math.max(...all.map((p) => p[1])) + margin,
    };
  }

  /**
   * Remember a road a racer drove, for its trail.
   * @param {object} racer Racer view.
   * @param {number} index Racer's pane index (sets its trail's side offset).
   * @param {number[]} src
   * @param {number[]} dst
   */
  addTrail(racer, index, src, dst) {
    if (!this.active) return;
    if (!this.trails.has(racer.id)) {
      this.trails.set(racer.id, { color: racer.color, spread: (index - 1) * TRAIL_SPREAD_PX, roads: [] });
    }
    this.trails.get(racer.id).roads.push([src, dst]);
  }

  /** Hide the map while a human still drives (they only know the exit's direction). */
  setLocked(locked) {
    this.canvas.hidden = locked;
  }

  /**
   * Draw the map and every car on it.
   * @param {{racer: object, car: import('../cars/car.js').Car}[]} entries
   * @param {number} time Wall-clock seconds (for the thinking pulse).
   */
  draw(entries, time) {
    if (!this.active || this.canvas.hidden) return;
    const { canvas, ctx, bounds } = this;
    const [width, height] = [canvas.clientWidth, canvas.clientHeight];
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const scale = Math.min(width / (bounds.right - bounds.left), height / (bounds.bottom - bounds.top));
    const center = [(bounds.left + bounds.right) / 2, (bounds.top + bounds.bottom) / 2];
    const toPx = ([x, y]) => [width / 2 + (x - center[0]) * scale, height / 2 + (y - center[1]) * scale];

    ctx.fillStyle = BACKGROUND;
    ctx.fillRect(0, 0, width, height);
    this.drawTiles(width, height, scale * ratio, center, scale);
    this.drawRoads(this.maze.closed, toPx, { color: CLOSED_COLOR, width: 4, dash: [5, 4] });
    const route = this.maze.optimal_path;
    this.drawRoads(
      route.slice(1).map((node, k) => [route[k], node]),
      toPx,
      { color: ROUTE_COLOR, width: 4, dash: [9, 6] },
    );
    for (const trail of this.trails.values()) {
      this.drawRoads(trail.roads, toPx, { color: trail.color, width: 3.5, spread: trail.spread });
    }
    this.drawPin(toPx(this.points.get(`${this.maze.start[0]},${this.maze.start[1]}`)), 'start');
    this.drawPin(toPx(this.points.get(`${this.maze.exit[0]},${this.maze.exit[1]}`)), 'finish');
    for (const { racer, car } of entries) this.drawCar(toPx(this.mercatorOf(car.object.position)), racer, car, time);
  }

  // ------------------------------------------------------------------ drawing

  /** Map pictures at the zoom that matches the drawing scale (sharpest without upscaling). */
  drawTiles(width, height, devicePerUnit, center, scale) {
    const zoom = this.zooms.find((z) => TILE_PX * 2 ** z >= devicePerUnit * 0.85) ?? this.zooms.at(-1);
    const count = 2 ** zoom;
    const left = center[0] - width / 2 / scale;
    const top = center[1] - height / 2 / scale;
    const size = scale / count;
    const { ctx } = this;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    for (let x = Math.floor(left * count); x < (left + width / scale) * count; x += 1) {
      for (let y = Math.floor(top * count); y < (top + height / scale) * count; y += 1) {
        // Overlap by a pixel so tile seams never show the background through.
        this.drawTile(zoom, x, y, (x / count - left) * scale, (y / count - top) * scale, size + 1);
      }
    }
  }

  /** One map picture; where it is missing, the matching part of a less zoomed one. */
  drawTile(zoom, x, y, left, top, size) {
    for (const lower of this.zooms.filter((z) => z <= zoom).reverse()) {
      const shift = 2 ** (zoom - lower);
      const image = this.tile(lower, Math.floor(x / shift), Math.floor(y / shift));
      if (image.complete && image.naturalWidth) {
        const part = TILE_PX / shift;
        const [sx, sy] = [(x % shift) * part, (y % shift) * part];
        this.ctx.drawImage(image, sx, sy, part, part, left, top, size, size);
        return;
      }
    }
  }

  drawRoads(roads, toPx, { color, width, dash = [], spread = 0 }) {
    const { ctx } = this;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineCap = 'round';
    ctx.setLineDash(dash);
    ctx.beginPath();
    for (const [src, dst] of roads) {
      const [ax, ay] = toPx(this.points.get(`${src[0]},${src[1]}`));
      const [bx, by] = toPx(this.points.get(`${dst[0]},${dst[1]}`));
      const length = Math.hypot(bx - ax, by - ay) || 1;
      const [nx, ny] = [(-(by - ay) / length) * spread, ((bx - ax) / length) * spread];
      ctx.moveTo(ax + nx, ay + ny);
      ctx.lineTo(bx + nx, by + ny);
    }
    ctx.stroke();
    ctx.restore();
  }

  /** Start: a white dot. Finish: a checkered flag square. */
  drawPin([x, y], kind) {
    const { ctx } = this;
    if (kind === 'start') {
      ctx.fillStyle = '#ffffff';
      ctx.strokeStyle = '#202124';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      return;
    }
    const cell = 4;
    for (let i = 0; i < 3; i += 1) {
      for (let j = 0; j < 3; j += 1) {
        ctx.fillStyle = (i + j) % 2 ? '#202124' : '#ffffff';
        ctx.fillRect(x - 1.5 * cell + i * cell, y - 1.5 * cell + j * cell, cell, cell);
      }
    }
    ctx.strokeStyle = '#202124';
    ctx.lineWidth = 1;
    ctx.strokeRect(x - 1.5 * cell, y - 1.5 * cell, 3 * cell, 3 * cell);
  }

  /** A dot in the racer's color (pulsing while its model thinks) with its label above. */
  drawCar([x, y], racer, car, time) {
    const { ctx } = this;
    const radius = car.thinking ? 6.5 + Math.abs(Math.sin(time * 8)) * 2 : 6.5;
    ctx.fillStyle = racer.color;
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    ctx.font = LABEL_FONT;
    const text = racer.label;
    const textWidth = ctx.measureText(text).width;
    ctx.fillStyle = 'rgba(32, 33, 36, 0.85)';
    ctx.fillRect(x - textWidth / 2 - 3, y - radius - 15, textWidth + 6, 12);
    ctx.fillStyle = racer.color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y - radius - 8.5);
  }

  // ------------------------------------------------------------------ helpers

  tile(zoom, x, y) {
    const key = `${zoom}/${x}/${y}`;
    if (!this.tiles.has(key)) {
      const image = new Image();
      image.src = `${TILES_URL}/${key}.png`;
      this.tiles.set(key, image);
    }
    return this.tiles.get(key);
  }

  /** Web Mercator point (0..1 across the world, y down) of a world position. */
  mercatorOf(position) {
    const [lat, lon] = this.geo.toLatLon(position.x, position.z);
    const sin = Math.sin((lat * Math.PI) / 180);
    return [(lon + 180) / 360, 0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)];
  }
}
