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

/** Asphalt with grain and a dashed center line running along V. */
export function roadTexture() {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(1);
  ctx.fillStyle = '#8a8a8a';
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ['#7e7e7e', '#969696', '#838383'], 220, rand);
  ctx.fillStyle = '#e9e4c8';
  ctx.fillRect(15, 2, 2, 12); // one dash per tile
  return toTexture(canvas);
}

/** Plain asphalt for junction squares (no center line). */
export function junctionTexture() {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(2);
  ctx.fillStyle = '#878787';
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ['#7b7b7b', '#939393'], 220, rand);
  return toTexture(canvas);
}

/** Red/white curb stripes. */
export function curbTexture() {
  const { canvas, ctx } = makeCanvas(8, 2);
  ctx.fillStyle = '#d63a2f';
  ctx.fillRect(0, 0, 4, 2);
  ctx.fillStyle = '#f4f4f4';
  ctx.fillRect(4, 0, 4, 2);
  return toTexture(canvas, 3, 1);
}

/** Leafy hedge with pink flower speckles near the bottom. */
export function hedgeTexture() {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(3);
  ctx.fillStyle = '#2f6b3a';
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ['#3b7d45', '#255a30', '#4a8c50', '#1f4d29'], 420, rand, 2);
  for (let i = 0; i < 26; i += 1) {
    ctx.fillStyle = rand() < 0.7 ? '#e48ab8' : '#f6f0f6';
    ctx.fillRect(Math.floor(rand() * 32), 24 + Math.floor(rand() * 8), 1, 1);
  }
  return toTexture(canvas);
}

/** Hedge top: darker, no flowers. */
export function hedgeTopTexture() {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(4);
  ctx.fillStyle = '#2a6234';
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ['#357540', '#21522b', '#3f8448'], 380, rand, 2);
  return toTexture(canvas);
}

/** Grass field. */
export function grassTexture(repeat) {
  const { canvas, ctx } = makeCanvas(32, 32);
  const rand = mulberry32(5);
  ctx.fillStyle = '#4f8a4a';
  ctx.fillRect(0, 0, 32, 32);
  speckle(ctx, 32, 32, ['#5a9654', '#447d40', '#63a05b'], 300, rand);
  return toTexture(canvas, repeat, repeat);
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
