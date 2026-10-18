/**
 * Overview-map decorations: a big colored marker over each car and a colored trail of
 * every road it drove. Comparing trails against the white A* dashes shows each model's
 * decision quality at a glance. Everything here is on the overview layer only.
 */

import * as THREE from 'three';
import { LAYERS } from '../config.js';
import { junctionToWorld } from '../geometry.js';

const TRAIL_WIDTH = 1.3;
const TRAIL_HEIGHT = 0.4;
const MARKER_HEIGHT = 9;

export class OverviewMarkers {
  /** @param {THREE.Scene} scene */
  constructor(scene) {
    this.group = new THREE.Group();
    scene.add(this.group);
    this.markers = new Map(); // racer id -> marker
    this.trailMaterials = new Map();
  }

  /** Remove everything (new race). */
  reset() {
    this.group.clear();
    this.markers.clear();
    this.trailMaterials.clear();
  }

  /**
   * @param {string} racerId
   * @param {string} color
   * @param {number} laneOffset Trails are offset like the cars so overlapping routes stay visible.
   */
  addRacer(racerId, color, laneOffset) {
    const marker = new THREE.Mesh(
      new THREE.CylinderGeometry(3.4, 3.4, 0.6, 16),
      new THREE.MeshBasicMaterial({ color }),
    );
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(3.6, 4.4, 20).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0xffffff }),
    );
    ring.position.y = 0.35;
    marker.add(ring);
    marker.traverse((child) => child.layers.set(LAYERS.overview));
    this.group.add(marker);
    this.markers.set(racerId, marker);
    this.trailMaterials.set(racerId, {
      material: new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.85 }),
      laneOffset,
    });
  }

  /**
   * Add one driven road to a racer's trail.
   * @param {string} racerId
   * @param {number[]} src
   * @param {number[]} dst
   */
  addTrail(racerId, src, dst) {
    const entry = this.trailMaterials.get(racerId);
    if (!entry) return;
    const a = junctionToWorld(src, TRAIL_HEIGHT);
    const b = junctionToWorld(dst, TRAIL_HEIGHT);
    const horizontal = a.z === b.z;
    const length = a.distanceTo(b) + TRAIL_WIDTH;
    const piece = new THREE.Mesh(
      new THREE.BoxGeometry(horizontal ? length : TRAIL_WIDTH, 0.15, horizontal ? TRAIL_WIDTH : length),
      entry.material,
    );
    piece.position.lerpVectors(a, b, 0.5);
    // Offset sideways (perpendicular to the road) so three racers' trails sit side by side.
    if (horizontal) piece.position.z += entry.laneOffset;
    else piece.position.x += entry.laneOffset;
    piece.layers.set(LAYERS.overview);
    this.group.add(piece);
  }

  /**
   * Keep markers above their cars.
   * @param {Map<string, import('../cars/car.js').Car>} cars
   * @param {number} time
   */
  update(cars, time) {
    for (const [id, marker] of this.markers) {
      const car = cars.get(id);
      if (!car) continue;
      marker.position.copy(car.object.position);
      marker.position.y = MARKER_HEIGHT + (car.thinking ? Math.abs(Math.sin(time * 8)) * 1.5 : 0);
      marker.scale.setScalar(car.thinking ? 1.15 : 1);
    }
  }
}
