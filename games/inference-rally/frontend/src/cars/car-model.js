/**
 * Procedural low-poly sports car, modelled facing -Z (north) with the rear at +Z,
 * so the chase camera sits on the +Z side. No model files needed.
 */

import * as THREE from 'three';
import { CAR } from '../config.js';
import { textTexture } from '../world/textures.js';

const DARK = 0x1c1c1f;
const GLASS = 0x2a3440;

/**
 * Build a car mesh group.
 * @param {{color: number|string, plate?: string, lights?: boolean}} options
 *   ``lights: false`` builds an unlit wreck (no tail/beacon lights).
 * @returns {THREE.Group} Group with ``userData.parts`` for animated pieces.
 */
export function buildCarModel({ color, plate = '', lights = true }) {
  const group = new THREE.Group();
  const paint = new THREE.MeshLambertMaterial({ color, flatShading: true });
  const dark = new THREE.MeshLambertMaterial({ color: DARK, flatShading: true });
  const glass = new THREE.MeshLambertMaterial({ color: GLASS, flatShading: true });
  const { length, width } = CAR;

  const body = box(width, 0.62, length, paint, 0, 0.62, 0);
  const nose = box(width * 0.96, 0.32, 1.0, paint, 0, 0.55, -length / 2 + 0.25);
  const cabin = taperedCabin(width * 0.8, 0.62, length * 0.42, paint);
  cabin.position.set(0, 1.24, 0.25);
  const rearWindow = box(width * 0.66, 0.42, 0.06, glass, 0, 1.26, length * 0.21 + 0.27);
  const windshield = box(width * 0.66, 0.4, 0.06, glass, 0, 1.25, -length * 0.21 + 0.2);
  windshield.rotation.x = 0.55;
  rearWindow.rotation.x = -0.6;
  const spoilerWing = box(width * 0.95, 0.08, 0.5, dark, 0, 1.25, length / 2 - 0.25);
  const spoilerPosts = [-0.6, 0.6].map((x) => box(0.08, 0.3, 0.08, dark, x, 1.05, length / 2 - 0.25));
  const bumper = box(width * 1.02, 0.25, 0.25, dark, 0, 0.42, length / 2);
  group.add(body, nose, cabin, rearWindow, windshield, spoilerWing, ...spoilerPosts, bumper);

  for (const [x, z] of [
    [-1, -1],
    [1, -1],
    [-1, 1],
    [1, 1],
  ]) {
    const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.42, 0.34, 8), dark);
    wheel.rotation.z = Math.PI / 2;
    wheel.position.set(x * (width / 2 - 0.05), 0.42, z * (length / 2 - 0.85));
    group.add(wheel);
  }

  const parts = { tailLights: [], beacon: null };
  if (lights) {
    for (const x of [-0.65, 0.65]) {
      const tail = box(0.5, 0.16, 0.06, new THREE.MeshBasicMaterial({ color: 0x7a1010 }), x, 0.78, length / 2 + 0.01);
      parts.tailLights.push(tail);
      group.add(tail);
    }
    // Roof beacon: pulses while the model is thinking (the car's "brain" light).
    parts.beacon = box(0.42, 0.22, 0.42, new THREE.MeshBasicMaterial({ color }), 0, 1.66, 0.35);
    group.add(parts.beacon);
  }

  if (plate) {
    const plateMesh = new THREE.Mesh(
      new THREE.PlaneGeometry(0.9, 0.24),
      new THREE.MeshBasicMaterial({ map: textTexture(plate, { width: 64, height: 16, color: '#111111', background: '#f2f2f2' }) }),
    );
    plateMesh.position.set(0, 0.5, length / 2 + 0.14);
    group.add(plateMesh);
  }

  group.traverse((child) => {
    if (child.isMesh) child.castShadow = true;
  });
  group.userData.parts = parts;
  return group;
}

function box(w, h, d, material, x, y, z) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
  mesh.position.set(x, y, z);
  return mesh;
}

/** Cabin with a narrower roof than its base, for a sporty silhouette. */
function taperedCabin(width, height, depth, material) {
  const geometry = new THREE.BoxGeometry(width, height, depth);
  const position = geometry.attributes.position;
  for (let i = 0; i < position.count; i += 1) {
    if (position.getY(i) > 0) {
      position.setX(i, position.getX(i) * 0.82);
      position.setZ(i, position.getZ(i) * 0.7);
    }
  }
  geometry.computeVertexNormals();
  return new THREE.Mesh(geometry, material);
}
