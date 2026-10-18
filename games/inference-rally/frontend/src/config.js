/**
 * Visual and gameplay tuning in one place. Nothing here affects race results —
 * the server decides everything; these values only change how it looks and sounds.
 */

/** World units per maze tile. A junction and each road between junctions is one tile. */
export const TILE = 10;

/** Distance between two adjacent junctions in world units (junction tile + road tile). */
export const BLOCK = TILE * 2;

export const WORLD = Object.freeze({
  hedgeHeight: 4.6,
  treeChance: 0.32,
  curbWidth: 0.7,
  curbHeight: 0.22,
  outerRing: 3, // extra hedge tiles around the maze so the edge is not visible
  groundSize: 2400,
});

export const CAMERA = Object.freeze({
  fov: 62,
  back: 8.5, // distance behind the car
  height: 3.1,
  lookAhead: 7,
  followRate: 5.5, // higher = snappier chase camera
  thinkingRise: 1.6, // camera lifts a little while the model thinks, to show the junction
});

export const CAR = Object.freeze({
  length: 4.4,
  width: 2.1,
  turnPortion: 0.22, // share of a segment spent rotating at the junction
  uturnPortion: 0.42,
  jumpHeight: 6, // peak of a rogue's hop over a hedge (world units)
});

/** The rogue's scouting drone (UNFAIR mode): where it hovers relative to its car. */
export const DRONE = Object.freeze({
  height: 3.6, // above the road, in view of the chase camera
  ahead: 4, // in front of the car, scouting the junction
  liftSeconds: 0.6, // time to rise off the roof
});

export const RENDER = Object.freeze({
  retroPixelScale: 3, // render at 1/3 resolution and upscale with crisp pixels
  clearColor: 0x0b0d12,
  fogNear: 70,
  fogFar: 560,
  shadowMapSize: 2048,
});

export const COLORS = Object.freeze({
  skyTop: 0x7fa7d6,
  skyHorizon: 0xdfe5e8,
  grass: 0x4f8a4a,
  grassDark: 0x3f7a3d,
  hedge: 0x2f6b3a,
  road: 0x8c8c8c,
  curbRed: 0xd63a2f,
  curbWhite: 0xf4f4f4,
  hills: [0x6f9f78, 0x7fae86, 0x8fb79b, 0x5f8f6c],
  fire: 0xffa62b,
});

/** UI modes and the racers.yaml profile each one races. */
export const MODES = Object.freeze({
  demo: { profile: 'demo', label: 'DEMO' },
  play: { profile: 'play', label: 'PLAY' },
  unfair: { profile: 'unfair', label: 'UNFAIR' },
});

/** Order the G key cycles through. */
export const MODE_CYCLE = Object.freeze(['demo', 'play', 'unfair']);

/** Arrow keys for the human player. */
export const HUMAN_KEYS = Object.freeze({
  ArrowLeft: 'left',
  ArrowUp: 'straight',
  ArrowRight: 'right',
  ArrowDown: 'back',
});

export const HUD = Object.freeze({
  tpsBarMax: 40, // tokens/sec that fills the red bar completely
  tpsSegments: 20,
  bubbleMs: 2600, // how long a "why" thought bubble stays up
  bannerMs: 1800,
  feedLength: 5,
  humanTimeoutS: 20, // must match HUMAN_TIMEOUT_S on the server
});

/** Layers: 0 = shared world, 1 = overview-only markers, 2+i = racer i's option arrows. */
export const LAYERS = Object.freeze({ world: 0, overview: 1, racerBase: 2 });

/** Events older than this (seconds) are replays after a reconnect: animate, but stay silent. */
export const STALE_EVENT_S = 0.6;
