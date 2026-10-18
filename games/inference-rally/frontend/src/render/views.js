/**
 * Split-screen rendering: one WebGL renderer, one viewport per pane.
 *
 * Racer panes use chase cameras; the overview pane uses an orthographic top-down
 * camera that also sees the overview-only layer (markers, trails, A* route).
 * Retro mode renders at a fraction of the resolution and lets CSS upscale the canvas
 * with crisp pixels.
 */

import * as THREE from 'three';
import { BLOCK, CAMERA, LAYERS, RENDER } from '../config.js';
import { junctionToWorld } from '../geometry.js';

const OVERVIEW_MAP_SHARE = 0.56; // left share of the overview pane used by the map
const OVERVIEW_MARGIN = 1.08;

const CAMERA_PROBE_STEP = 0.5;
const CAMERA_WALL_MARGIN = 1.2;
const CAMERA_MIN_BACK = 3;
const VICTORY_ORBIT_SPEED = 0.45; // radians/sec
const VICTORY_RADIUS = 11;
const VICTORY_HEIGHT = 7.5;

/** Longest distance (<= max) behind ``origin`` along ``-forward`` that stays on the road. */
function clearDistance(origin, forward, max, isOpen) {
  for (let d = CAMERA_PROBE_STEP; d <= max; d += CAMERA_PROBE_STEP) {
    const x = origin.x - forward.x * d;
    const z = origin.z - forward.z * d;
    if (!isOpen(x, z)) return Math.max(CAMERA_MIN_BACK, d - CAMERA_WALL_MARGIN);
  }
  return max;
}

export class SplitViews {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {HTMLElement[]} panes Pane elements in order: racer 0..n-1, then overview.
   */
  constructor(canvas, panes) {
    this.canvas = canvas;
    this.panes = panes;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance' });
    this.renderer.setClearColor(RENDER.clearColor);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    // Shadows are identical in every viewport: render the shadow map once per frame.
    this.renderer.shadowMap.autoUpdate = false;
    this.renderer.setScissorTest(true);
    this.retro = true;
    this.mapLocked = false; // play mode hides the overview map from the human

    this.chaseCameras = [];
    this.overviewCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 1000);
    this.overviewCamera.up.set(0, 0, -1); // north at the top of the map
    this.overviewCamera.layers.enable(LAYERS.overview);
    this.mapCenter = new THREE.Vector3();
    this.mapSize = 100;

    this.resize();
    window.addEventListener('resize', () => this.resize());
  }

  /** Toggle chunky pixels. @returns {boolean} new state */
  toggleRetro() {
    this.retro = !this.retro;
    this.resize();
    return this.retro;
  }

  resize() {
    const { clientWidth, clientHeight } = this.canvas;
    const ratio = this.retro ? 1 / RENDER.retroPixelScale : Math.min(window.devicePixelRatio, 2);
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(clientWidth, clientHeight, false);
    this.canvas.classList.toggle('pixelated', this.retro);
  }

  /**
   * Prepare cameras for a new race.
   * @param {number} racerCount
   * @param {object} maze Maze view (for framing the overview map).
   */
  setup(racerCount, maze) {
    this.chaseCameras = Array.from({ length: racerCount }, (_, index) => {
      const camera = new THREE.PerspectiveCamera(CAMERA.fov, 1, 0.5, 1400);
      camera.layers.enable(LAYERS.racerBase + index);
      camera.userData.initialized = false;
      return camera;
    });
    const a = junctionToWorld([0, 0]);
    const b = junctionToWorld([maze.width - 1, maze.height - 1]);
    this.mapCenter.lerpVectors(a, b, 0.5);
    this.mapSize = Math.max(maze.width, maze.height) * BLOCK * OVERVIEW_MARGIN;
    this.overviewCamera.position.set(this.mapCenter.x, 300, this.mapCenter.z);
    this.overviewCamera.lookAt(this.mapCenter);
  }

  /**
   * Smoothly move each chase camera behind its car, pulling in closer when a hedge is
   * between camera and car (otherwise turns would put the camera inside a wall).
   * @param {import('../cars/car.js').Car[]} cars In pane order.
   * @param {number} dt
   * @param {(x: number, z: number) => boolean} isOpen Road test from the maze builder.
   */
  updateCameras(cars, dt, isOpen) {
    const follow = 1 - Math.exp(-dt * CAMERA.followRate);
    cars.forEach((car, index) => {
      const camera = this.chaseCameras[index];
      if (!camera) return;
      const forward = car.forward;
      const origin = car.object.position;
      if (car.finished) {
        this.orbit(camera, car, dt);
        return;
      }
      const back = clearDistance(origin, forward, CAMERA.back, isOpen);
      // When squeezed close, look from higher up so the car stays in view.
      const squeeze = 1 - back / CAMERA.back;
      const rise = (car.thinking ? CAMERA.thinkingRise : 0) + squeeze * 2.5;
      // Rise with the car during a rogue's hedge jump, so it stays in frame mid-air.
      const desired = origin.clone().addScaledVector(forward, -back).setY(CAMERA.height + rise + car.hop);
      const target = origin.clone().addScaledVector(forward, CAMERA.lookAhead).setY(1.2 + car.hop);

      if (!camera.userData.initialized) {
        camera.position.copy(desired);
        camera.userData.lookAt = target.clone();
        camera.userData.initialized = true;
      }
      camera.position.lerp(desired, follow);
      // Never let the smoothed position drift into a hedge either.
      if (!isOpen(camera.position.x, camera.position.z)) camera.position.lerp(desired, 0.5);
      camera.userData.lookAt.lerp(target, follow);
      camera.lookAt(camera.userData.lookAt);
    });
  }

  /** Victory lap: slowly circle a finished car from above the hedges. */
  orbit(camera, car, dt) {
    camera.userData.orbit = (camera.userData.orbit ?? car.yaw + Math.PI) + dt * VICTORY_ORBIT_SPEED;
    const angle = camera.userData.orbit;
    const desired = car.object.position
      .clone()
      .add(new THREE.Vector3(Math.sin(angle) * VICTORY_RADIUS, VICTORY_HEIGHT, Math.cos(angle) * VICTORY_RADIUS));
    camera.position.lerp(desired, 1 - Math.exp(-dt * 2));
    camera.userData.lookAt.lerp(car.object.position.clone().setY(1), 1 - Math.exp(-dt * 3));
    camera.lookAt(camera.userData.lookAt);
  }

  /**
   * Draw every pane.
   * @param {THREE.Scene} scene
   */
  render(scene) {
    const renderer = this.renderer;
    const canvasRect = this.canvas.getBoundingClientRect();
    const height = canvasRect.height;

    renderer.setScissor(0, 0, canvasRect.width, height);
    renderer.setViewport(0, 0, canvasRect.width, height);
    renderer.clear();
    renderer.shadowMap.needsUpdate = true;

    this.chaseCameras.forEach((camera, index) => {
      const rect = this.paneRect(index, canvasRect);
      if (!rect) return;
      camera.aspect = rect.width / rect.height;
      camera.updateProjectionMatrix();
      this.draw(scene, camera, rect, height);
    });

    const overview = this.paneRect(this.panes.length - 1, canvasRect);
    if (overview && !this.mapLocked) {
      const map = { ...overview, width: overview.width * OVERVIEW_MAP_SHARE };
      const aspect = map.width / map.height;
      const half = this.mapSize / 2;
      Object.assign(this.overviewCamera, {
        left: -half * Math.max(1, aspect),
        right: half * Math.max(1, aspect),
        top: half / Math.min(1, aspect),
        bottom: -half / Math.min(1, aspect),
      });
      this.overviewCamera.updateProjectionMatrix();
      this.draw(scene, this.overviewCamera, map, height);
    }
  }

  draw(scene, camera, rect, canvasHeight) {
    // WebGL viewports start at the bottom-left; DOM rects at the top-left.
    const y = canvasHeight - rect.top - rect.height;
    this.renderer.setScissor(rect.left, y, rect.width, rect.height);
    this.renderer.setViewport(rect.left, y, rect.width, rect.height);
    this.renderer.render(scene, camera);
    this.renderer.shadowMap.needsUpdate = false;
  }

  paneRect(index, canvasRect) {
    const pane = this.panes[index];
    if (!pane || pane.hidden) return null;
    const rect = pane.getBoundingClientRect();
    return {
      left: rect.left - canvasRect.left,
      top: rect.top - canvasRect.top,
      width: rect.width,
      height: rect.height,
    };
  }
}
