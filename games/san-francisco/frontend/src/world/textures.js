/**
 * Procedural pixel-art textures drawn on small canvases.
 * Small sizes + nearest-neighbor filtering give the chunky retro arcade look and mean
 * the game needs no image downloads at all (works offline at the venue).
 */

import * as THREE from 'three';

/** Deterministic PRNG so textures look identical on every load. */
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function makeCanvas(width, height) {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return { canvas, ctx: canvas.getContext('2d') };
}

function toTexture(canvas, repeatX = 1, repeatY = 1) {
  const texture = new THREE.CanvasTexture(canvas);
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestMipmapNearestFilter;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(repeatX, repeatY);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function speckle(ctx, width, height, colors, count, rand, size = 1) {
  for (let i = 0; i < count; i += 1) {
    ctx.fillStyle = colors[Math.floor(rand() * colors.length)];
    ctx.fillRect(Math.floor(rand() * width), Math.floor(rand() * height), size, size);
  }
}

const ASPHALT = ['#5d5f62', '#55575a', '#666869', '#4f5154'];
const TRIM = '#f4efe4';
const GLASS = '#2c3a4a';
const GLINT = '#8aa4ba';
const FLOOR_PX = 11; // facade pixels per floor
const CORNICE_PX = 3;
const FOUNDATION_PX = 2;

/** City street running along V: double yellow center line, a crosswalk at each end. */
export function roadTexture() {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(1);
  ctx.fillStyle = ASPHALT[0];
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ASPHALT.slice(1), 220, rand);
  ctx.fillStyle = '#e8c547';
  ctx.fillRect(14, 6, 1, 20);
  ctx.fillRect(17, 6, 1, 20);
  ctx.fillStyle = '#e9e7df';
  for (let x = 7; x < 25; x += 4) {
    ctx.fillRect(x, 1, 2, 4);
    ctx.fillRect(x, 27, 2, 4);
  }
  return toTexture(canvas);
}

/** Plain asphalt for intersections, with a manhole cover. */
export function junctionTexture() {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(2);
  ctx.fillStyle = ASPHALT[0];
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ASPHALT.slice(1), 220, rand);
  ctx.fillStyle = '#3f4144';
  ctx.fillRect(20, 9, 4, 4);
  return toTexture(canvas);
}

/**
 * Concrete sidewalk slab with expansion joints; the curb edges are painted red at
 * intersections, as on San Francisco street corners.
 * @param {boolean} redCurb
 */
export function sidewalkTexture(redCurb) {
  const { canvas, ctx } = makeCanvas(32, 4);
  ctx.fillStyle = '#c2bdb4';
  ctx.fillRect(0, 0, 32, 4);
  ctx.fillStyle = '#a8a39a';
  for (let x = 0; x < 32; x += 8) ctx.fillRect(x, 0, 1, 4);
  ctx.fillStyle = redCurb ? '#c8372d' : '#9a958c';
  ctx.fillRect(0, 0, 32, 1);
  ctx.fillRect(0, 3, 32, 1);
  return toTexture(canvas);
}

/**
 * Row-house front in one paint color: a bay window and a side window on each floor, the
 * front door on the ground floor, white trim and a cornice. Covers one whole wall face.
 * @param {string} paint CSS color.
 * @param {number} floors
 */
export function facadeTexture(paint, floors) {
  const height = CORNICE_PX + floors * FLOOR_PX + FOUNDATION_PX;
  const { canvas, ctx } = makeCanvas(32, height);
  const accent = shade(paint, 0.72);
  ctx.fillStyle = paint;
  ctx.fillRect(0, 0, 32, height);
  ctx.fillStyle = shade(paint, 0.92);
  for (let y = CORNICE_PX + 1; y < height; y += 3) ctx.fillRect(0, y, 32, 1); // clapboard
  ctx.fillStyle = TRIM;
  ctx.fillRect(0, 0, 32, CORNICE_PX);
  ctx.fillStyle = accent;
  for (let x = 0; x < 32; x += 2) ctx.fillRect(x, CORNICE_PX - 1, 1, 1); // dentils

  for (let floor = 0; floor < floors; floor += 1) {
    const top = CORNICE_PX + floor * FLOOR_PX;
    bayWindow(ctx, 3, top + 1);
    if (floor === floors - 1) {
      frontDoor(ctx, 20, top + 1, accent);
    } else {
      sideWindow(ctx, 20, top + 1);
      ctx.fillStyle = TRIM;
      ctx.fillRect(0, top + FLOOR_PX - 1, 32, 1); // band between floors
    }
  }
  ctx.fillStyle = '#7c7770';
  ctx.fillRect(0, height - FOUNDATION_PX, 32, FOUNDATION_PX);
  ctx.fillStyle = '#d8d2c6';
  ctx.fillRect(20, height - FOUNDATION_PX, 8, FOUNDATION_PX); // front steps
  return toTexture(canvas);
}

function bayWindow(ctx, x, y) {
  ctx.fillStyle = TRIM;
  ctx.fillRect(x, y, 14, 8);
  for (const [dx, width] of [
    [1, 3],
    [5, 4],
    [10, 3],
  ]) {
    windowPane(ctx, x + dx, y + 1, width, 6);
  }
}

function sideWindow(ctx, x, y) {
  ctx.fillStyle = TRIM;
  ctx.fillRect(x, y, 8, 8);
  windowPane(ctx, x + 1, y + 1, 6, 2);
  windowPane(ctx, x + 1, y + 4, 6, 3);
}

function frontDoor(ctx, x, y, color) {
  ctx.fillStyle = TRIM;
  ctx.fillRect(x, y, 8, FLOOR_PX - 1);
  windowPane(ctx, x + 2, y + 1, 4, 1); // transom
  ctx.fillStyle = color;
  ctx.fillRect(x + 2, y + 3, 4, FLOOR_PX - 4);
}

function windowPane(ctx, x, y, width, height) {
  ctx.fillStyle = GLASS;
  ctx.fillRect(x, y, width, height);
  ctx.fillStyle = GLINT;
  ctx.fillRect(x, y, 1, 1);
}

/** Tar roof of the flats, with a parapet, a skylight and a vent. */
export function flatRoofTexture() {
  const { canvas, ctx } = makeCanvas(16, 16);
  const rand = mulberry32(3);
  ctx.fillStyle = '#aaa398';
  ctx.fillRect(0, 0, 16, 16);
  speckle(ctx, 16, 16, ['#b5aea3', '#9f988d'], 60, rand);
  ctx.strokeStyle = '#d3ccc0';
  ctx.strokeRect(0.5, 0.5, 15, 15);
  ctx.fillStyle = '#9fb3c4';
  ctx.fillRect(4, 4, 3, 2);
  ctx.fillStyle = '#6a6660';
  ctx.fillRect(10, 9, 2, 2);
  return toTexture(canvas);
}

/**
 * Office-tower window grid: one 8x8 cell covers four by four world units, so set the
 * geometry's UVs in world units / 4.
 * @param {string} wall CSS color.
 */
export function towerTexture(wall) {
  const { canvas, ctx } = makeCanvas(8, 8);
  ctx.fillStyle = wall;
  ctx.fillRect(0, 0, 8, 8);
  ctx.fillStyle = '#34495c';
  for (const x of [1, 5]) {
    for (const y of [1, 5]) ctx.fillRect(x, y, 2, 2);
  }
  return toTexture(canvas);
}

/**
 * Scale a geometry's UVs so a window texture repeats every 4 world units instead of once
 * per face.
 */
export function repeatEvery4(geometry, width, height) {
  const uv = geometry.attributes.uv;
  for (let k = 0; k < uv.count; k += 1) uv.setXY(k, (uv.getX(k) * width) / 4, (uv.getY(k) * height) / 4);
  return geometry;
}

/** Park lawn. */
export function grassTexture(repeat) {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(6);
  ctx.fillStyle = '#5a9a4f';
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ['#64a659', '#4f8a46', '#6db162'], 300, rand);
  return toTexture(canvas, repeat, repeat);
}

/** Orange and white diagonal stripes of a road-closed barricade board. */
export function barricadeTexture() {
  const { canvas, ctx } = makeCanvas(16, 4);
  for (let x = -4; x < 16; x += 1) {
    ctx.fillStyle = Math.floor((x + 8) / 4) % 2 ? '#f2f2f2' : '#f0782a';
    for (let y = 0; y < 4; y += 1) ctx.fillRect(x + y, y, 1, 1);
  }
  return toTexture(canvas);
}

/** Bay water with wave glints. */
export function waterTexture(repeat) {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(4);
  ctx.fillStyle = '#3e6f8e';
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ['#46789a', '#37637f'], 160, rand);
  ctx.fillStyle = '#6d98b4';
  for (let i = 0; i < 10; i += 1) ctx.fillRect(Math.floor(rand() * 29), Math.floor(rand() * 32), 3, 1);
  return toTexture(canvas, repeat, repeat);
}

/** Distant city seen from above: one block of rooftops between two streets, per repeat. */
export function cityGroundTexture(repeatX, repeatY) {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(5);
  ctx.fillStyle = ASPHALT[0];
  ctx.fillRect(0, 0, 32, 32);
  ctx.fillStyle = '#b8b3aa';
  ctx.fillRect(5, 5, 27, 27); // sidewalk around the block
  const roofs = ['#8d8a86', '#a9a39b', '#c7b9a6', '#9aa3ab', '#b39a8c', '#7f7c78'];
  for (let x = 6; x < 31; x += 6) {
    for (let y = 6; y < 31; y += 6) {
      ctx.fillStyle = roofs[Math.floor(rand() * roofs.length)];
      ctx.fillRect(x, y, 5, 5);
    }
  }
  return toTexture(canvas, repeatX, repeatY);
}

/** Two-color bands along V (the Sutro Tower's red and white). */
export function stripeTexture(colorA, colorB, bands) {
  const { canvas, ctx } = makeCanvas(1, 2);
  ctx.fillStyle = colorA;
  ctx.fillRect(0, 0, 1, 1);
  ctx.fillStyle = colorB;
  ctx.fillRect(0, 1, 1, 1);
  return toTexture(canvas, 1, bands);
}

/** Scale a CSS hex color's brightness, e.g. a darker trim of the same paint. */
function shade(hex, factor) {
  const value = parseInt(hex.slice(1), 16);
  const channel = (shift) => Math.min(255, Math.round(((value >> shift) & 255) * factor));
  return `rgb(${channel(16)}, ${channel(8)}, ${channel(0)})`;
}

/** Black/white checkerboard for the start line and the finish banner. */
export function checkerTexture(columns = 8, rows = 2) {
  const { canvas, ctx } = makeCanvas(columns * 4, rows * 4);
  for (let x = 0; x < columns; x += 1) {
    for (let y = 0; y < rows; y += 1) {
      ctx.fillStyle = (x + y) % 2 ? '#111111' : '#f4f4f4';
      ctx.fillRect(x * 4, y * 4, 4, 4);
    }
  }
  return toTexture(canvas);
}

/** Soft blobby cloud sprite. */
export function cloudTexture() {
  const { canvas, ctx } = makeCanvas(64, 24);
  const rand = mulberry32(6);
  for (let i = 0; i < 14; i += 1) {
    const x = 10 + rand() * 44;
    const y = 8 + rand() * 8;
    const r = 4 + rand() * 7;
    ctx.fillStyle = rand() < 0.3 ? '#e6eaee' : '#ffffff';
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
  const texture = toTexture(canvas);
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  return texture;
}

/** Flame sprite frame (pixel fire); several are layered and flickered. */
export function flameTexture(seed) {
  const { canvas, ctx } = makeCanvas(16, 24);
  const rand = mulberry32(seed);
  const palette = ['#fff3a0', '#ffd23f', '#ff9f1c', '#f25c05', '#c2280a'];
  for (let y = 0; y < 24; y += 1) {
    const width = Math.max(0, Math.round((y / 24) * 7 + rand() * 3));
    for (let x = 8 - width; x < 8 + width; x += 1) {
      const heat = (y / 24) * 3 + (1 - Math.abs(x - 8) / 8) * 2 + rand() * 1.2;
      ctx.fillStyle = palette[Math.max(0, Math.min(4, 5 - Math.round(heat)))];
      ctx.fillRect(x, y, 1, 1);
    }
  }
  const texture = toTexture(canvas);
  texture.wrapS = THREE.ClampToEdgeWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  return texture;
}

/**
 * Text drawn into a texture (used for the FINISH banner and number plates).
 * @param {string} text
 * @param {{width?: number, height?: number, color?: string, background?: string}} [options]
 */
export function textTexture(text, { width = 128, height = 24, color = '#ffffff', background = '#111111' } = {}) {
  const { canvas, ctx } = makeCanvas(width, height);
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = color;
  ctx.font = `bold ${Math.floor(height * 0.7)}px monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, width / 2, height / 2 + 1);
  const texture = toTexture(canvas);
  texture.wrapS = THREE.ClampToEdgeWrapping;
  return texture;
}
