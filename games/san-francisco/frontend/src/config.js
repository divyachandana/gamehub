/**
 * Visual and gameplay tuning in one place. Nothing here affects race results —
 * the server decides everything; these values only change how it looks and sounds.
 */

/** World units per maze tile. A junction and each road between junctions is one tile. */
export const TILE = 10;

/** Distance between two adjacent junctions in world units (junction tile + road tile). */
export const BLOCK = TILE * 2;

export const WORLD = Object.freeze({
  houseHeight: 8, // two-floor painted-lady row houses
  flatsHeight: 12, // three-floor flats
  roofHeight: 3, // gable on top of each row house
  flatsNearStart: 0.12, // share of flats at the start corner (south-west)...
  flatsNearExit: 0.7, // ...rising toward the exit corner (north-east, downtown)
  sidewalkWidth: 1.4,
  sidewalkHeight: 0.25,
  outerRing: 3, // extra city blocks around the maze so the edge is not visible
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
  fogNear: 80,
  fogFar: 900, // far enough that the Golden Gate Bridge still shows through the fog
  shadowMapSize: 2048,
});

export const COLORS = Object.freeze({
  skyTop: 0x6f9fd6,
  skyHorizon: 0xe2e0da, // pale grey: the bay fog
  hills: [0xa8975f, 0x8f8a55, 0x6f7f4a, 0xb9a66e], // dry golden grass, darker oak patches
  // Facade paints are drawn on canvases, so they are CSS strings.
  housePaints: ['#b9a3d6', '#9fd8b8', '#f2d77e', '#9ec5e8', '#f0a7a0', '#7cc6c1', '#efe3c8'],
  flatsPaints: ['#e8dcc0', '#d9a07f', '#c9ccd1', '#f2efe6', '#b8c9a3'],
  roofs: [0xa0624c, 0x9a9fab, 0xb09478, 0x8f9a88], // light, so dark streets stand out on the map
  towers: ['#c9ccd0', '#d8cfbf', '#8fa6b8', '#b5b9bd', '#e6e2d8'],
  goldenGate: 0xc0362c, // International Orange
});

/**
 * Real places are set by latitude/longitude and land where they really are around the
 * raced area. Inside the street grid, real meters map onto the junctions; beyond it,
 * distances shrink smoothly so far landmarks still fit inside the fog.
 */
export const GEO = Object.freeze({
  squeezeM: 3000, // beyond the grid, the first 3 km take about two thirds of the room
  // Random grids (no street map) pretend to sit in North Beach.
  fallbackOrigin: [37.80085, -122.4115],
  fallbackRotationDeg: 9.7,
  fallbackSpacingM: [146, 104],
  // Real heights are squeezed too (h = scale * meters^power), so towers do not dwarf the race.
  heightScale: 2.0,
  heightPower: 0.7,
  footprintScale: 1.0,
  footprintPower: 0.8,
});

/**
 * Map view: the race drawn on the real map (OpenStreetMap's map pictures on the ground,
 * grey buildings at their real heights, cars on the real streets), seen like a navigation
 * app. Used whenever the race has a street map; add ?view=city to the address for the
 * pixel city instead.
 */
export const MAP_VIEW = Object.freeze({
  unitsPerMeter: 0.5, // real streets (about 21 m) come out about as wide as the pixel city's
  pictureZoom: 16, // map pictures fill the view beyond the drawn map
  groundColor: 0xe8e4dc, // OpenStreetMap's land color, beyond the pictures
  landColor: 0xf1efe9,
  parkColor: 0xc8e6c9,
  roadEdgeColor: 0xd6d3cc,
  labelColor: '#5f6368', // canvas text, so a CSS color
  // Street widths in world units: wide enough for three cars side by side.
  roadWidths: { primary: 13, trunk: 13, secondary: 12.5, tertiary: 12, default: 11 },
  buildingColor: 0xf2efe8,
  buildingShadeLift: 0x5c5850, // keeps shaded walls light grey, as on Google's 3D map
  // Steep and close, like a navigation app, so buildings hide little of the street.
  camera: { fov: 50, back: 10, height: 21, lookAhead: 7, followRate: 9, thinkingRise: 3 },
  fogNear: 260,
  fogFar: 1500,
});

/** San Francisco landmarks: [lat, lon]. */
export const LANDMARKS = Object.freeze({
  goldenGateTowers: [
    [37.814, -122.4779],
    [37.8255, -122.4794],
  ],
  bayBridgeTowers: [
    [37.7935, -122.38],
    [37.802, -122.371],
  ],
  transamerica: [37.7952, -122.4028],
  salesforce: [37.7897, -122.3972],
  coitTower: [37.8024, -122.4058],
  sutroTower: [37.7552, -122.4528],
  alcatraz: [37.8267, -122.423],
});

/** Hills: [lat, lon, radius, height] (radius and height in world units). */
export const HILLS = Object.freeze([
  [37.7544, -122.4477, 70, 58], // Twin Peaks
  [37.7531, -122.441, 60, 50],
  [37.8277, -122.4995, 110, 95], // Marin Headlands
  [37.8455, -122.478, 120, 110],
  [37.855, -122.5205, 130, 120],
  [37.8609, -122.4326, 80, 70], // Angel Island
  [37.8098, -122.3647, 30, 26], // Yerba Buena Island
  [37.865, -122.24, 160, 90], // East Bay hills
  [37.82, -122.21, 160, 80],
  [37.775, -122.18, 150, 70],
  [37.69, -122.43, 150, 70], // San Bruno Mountain
]);

/** San Francisco's shoreline, [lat, lon] around the city's land; water everywhere else. */
export const SHORE = Object.freeze([
  [37.8105, -122.477], // Fort Point
  [37.8055, -122.462], // Crissy Field
  [37.8065, -122.44], // Marina
  [37.8075, -122.431], // Fort Mason
  [37.808, -122.422], // Aquatic Park
  [37.8087, -122.4098], // Pier 39
  [37.806, -122.403],
  [37.802, -122.399],
  [37.7955, -122.3935], // Ferry Building
  [37.788, -122.388],
  [37.7785, -122.387],
  [37.77, -122.386], // Mission Bay
  [37.759, -122.383],
  [37.728, -122.36], // Hunters Point
  [37.71, -122.38],
  [37.6, -122.37],
  [37.6, -122.5],
  [37.735, -122.507], // Ocean Beach
  [37.77, -122.511],
  [37.787, -122.506], // Lands End
  [37.793, -122.484], // Baker Beach
]);

/** UI modes and the racers.yaml profile each one races. */
export const MODES = Object.freeze({
  demo: { profile: 'demo', label: 'DEMO' },
  play: { profile: 'play', label: 'PLAY' },
  unfair: { profile: 'unfair', label: 'UNFAIR' },
});

/** This game (the server's games: in racers.yaml, by key). */
export const GAMES = Object.freeze({
  sf: { title: 'San Francisco', logo: 'SAN <span>FRANCISCO</span>', titleLogo: 'SAN<br><span>FRANCISCO</span>' },
});

/** The game hub launcher (hub/), which starts this game and Inference Rally. */
export const HUB_PORT = 8000;

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
