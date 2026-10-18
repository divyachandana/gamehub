/**
 * Procedural low-poly quadcopter for the rogue's drone peek (UNFAIR mode): it rises over
 * the car, hovers while it scouts, and its rotors spin. No model files needed.
 */

import * as THREE from 'three';

const DARK = 0x1c1c1f;
const SCAN = 0x5ef2ff;
const ARM = 1.1; // rotor distance from the body centre

/**
 * Build a drone mesh group.
 * @param {number|string} color Body color (the racer's color).
 * @returns {THREE.Group} Group with ``userData.rotors`` and ``userData.scan`` to animate.
 */
export function buildDroneModel(color) {
  const group = new THREE.Group();
  const paint = new THREE.MeshLambertMaterial({ color, flatShading: true });
  const dark = new THREE.MeshLambertMaterial({ color: DARK, flatShading: true });

  group.add(mesh(new THREE.BoxGeometry(0.9, 0.3, 0.9), paint));
  const rotors = [];
  for (const [x, z] of [
    [-1, -1],
    [1, -1],
    [-1, 1],
    [1, 1],
  ]) {
    const arm = mesh(new THREE.BoxGeometry(0.12, 0.08, ARM * 1.4), dark);
    arm.position.set((x * ARM) / 2, 0, (z * ARM) / 2);
    arm.rotation.y = x * z > 0 ? -Math.PI / 4 : Math.PI / 4;
    const rotor = mesh(new THREE.BoxGeometry(1.1, 0.04, 0.16), dark);
    rotor.position.set(x * ARM * 0.72, 0.22, z * ARM * 0.72);
    rotors.push(rotor);
    group.add(arm, rotor);
  }

  // Scanning beam: a translucent cone pointing down at the maze.
  const scan = mesh(
    new THREE.ConeGeometry(2.2, 5, 12, 1, true),
    new THREE.MeshBasicMaterial({ color: SCAN, transparent: true, opacity: 0.22, depthWrite: false }),
  );
  scan.position.y = -2.6;
  group.add(scan);

  group.userData = { rotors, scan };
  return group;
}

function mesh(geometry, material) {
  return new THREE.Mesh(geometry, material);
}
