/**
 * Floating chevrons on the road at a junction showing the roads a model can choose.
 * When the model answers, the chosen arrow lights up in the racer's color and the
 * rest fade — the decision is visible in 3D, not just in text.
 *
 * Arrows live on a per-racer layer, so each racer's chase camera only sees its own.
 */

import * as THREE from 'three';
import { headingToVector, junctionToWorld, turnHeading } from '../geometry.js';

const ARROW_DISTANCE = 3.6;
const FADE_RATE = 5;

function chevronGeometry() {
  const shape = new THREE.Shape();
  shape.moveTo(0, 1.1);
  shape.lineTo(1.1, -0.1);
  shape.lineTo(0.6, -0.1);
  shape.lineTo(0, 0.55);
  shape.lineTo(-0.6, -0.1);
  shape.lineTo(-1.1, -0.1);
  shape.closePath();
  // Lie flat with the tip pointing north (-Z).
  return new THREE.ShapeGeometry(shape).rotateX(-Math.PI / 2);
}

const CHEVRON = chevronGeometry();

export class OptionArrows {
  /**
   * @param {THREE.Scene} scene
   * @param {string} color Racer color.
   * @param {number} layer Render layer only this racer's camera sees.
   */
  constructor(scene, color, layer) {
    this.group = new THREE.Group();
    this.group.layers.set(layer);
    this.layer = layer;
    this.color = new THREE.Color(color);
    this.arrows = new Map(); // move -> mesh
    this.chosen = null;
    scene.add(this.group);
  }

  /**
   * Show one chevron per available move.
   * @param {number[]} node Junction.
   * @param {string} heading Car heading on arrival.
   * @param {string[]} moves Available relative moves.
   */
  show(node, heading, moves) {
    this.clear();
    const center = junctionToWorld(node, 0.35);
    for (const move of moves) {
      if (move === 'back') continue; // turning back is always possible; keep the view clean
      const direction = headingToVector(turnHeading(heading, move));
      const material = new THREE.MeshBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.9,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(CHEVRON, material);
      mesh.position.copy(center).addScaledVector(direction, ARROW_DISTANCE);
      mesh.rotation.y = Math.atan2(-direction.x, -direction.z);
      mesh.layers.set(this.layer);
      mesh.userData.baseY = mesh.position.y;
      this.group.add(mesh);
      this.arrows.set(move, mesh);
    }
  }

  /** Highlight the model's choice. @param {string} move */
  choose(move) {
    this.chosen = move;
  }

  clear() {
    for (const mesh of this.arrows.values()) {
      mesh.material.dispose();
      this.group.remove(mesh);
    }
    this.arrows.clear();
    this.chosen = null;
  }

  /** @param {number} time @param {number} dt */
  update(time, dt) {
    for (const [move, mesh] of this.arrows) {
      const isChosen = this.chosen === move;
      const target = this.chosen === null ? 0.9 : isChosen ? 1 : 0.12;
      mesh.material.opacity += (target - mesh.material.opacity) * (1 - Math.exp(-dt * FADE_RATE));
      if (isChosen) mesh.material.color.copy(this.color);
      const bob = this.chosen === null ? Math.sin(time * 5 + mesh.position.x) * 0.12 : 0;
      mesh.position.y = mesh.userData.baseY + bob;
      mesh.scale.setScalar(isChosen ? 1.25 + Math.sin(time * 12) * 0.08 : 1);
    }
  }
}
