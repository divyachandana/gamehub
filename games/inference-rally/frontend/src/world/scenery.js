/**
 * Everything outside the maze: sky gradient, distant hills, clouds, grass field, lights.
 * Gives the arcade "open road" feeling above the hedge walls.
 */

import * as THREE from 'three';
import { COLORS, RENDER, WORLD } from '../config.js';
import { cloudTexture, grassTexture } from './textures.js';

const HILL_COUNT = 34;
const CLOUD_COUNT = 22;

/**
 * Add sky, fog, lights, ground, hills and clouds to ``scene``.
 * @param {THREE.Scene} scene
 * @param {THREE.Vector3} center World center of the maze (scenery is placed around it).
 * @returns {{sun: THREE.DirectionalLight, update: (time: number) => void}}
 */
export function buildScenery(scene, center) {
  scene.fog = new THREE.Fog(COLORS.skyHorizon, RENDER.fogNear, RENDER.fogFar);
  scene.add(buildSkyDome(center));

  const hemi = new THREE.HemisphereLight(0xdfeaff, 0x4a6b3a, 1.15);
  scene.add(hemi);
  const sun = buildSun(center);
  scene.add(sun, sun.target);

  // The ground sits well below the road tiles, is subdivided (huge triangles lose depth
  // precision when clipped) and is pushed back with a polygon offset, so roads never
  // flicker through to grass on any GPU.
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(WORLD.groundSize, WORLD.groundSize, 48, 48),
    new THREE.MeshLambertMaterial({
      map: grassTexture(WORLD.groundSize / 8),
      polygonOffset: true,
      polygonOffsetFactor: 2,
      polygonOffsetUnits: 4,
    }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.set(center.x, -0.25, center.z);
  ground.receiveShadow = true;
  scene.add(ground);

  scene.add(buildHills(center));
  const clouds = buildClouds(center);
  scene.add(clouds);

  return {
    sun,
    update(time) {
      // Slow drift keeps the sky alive without distracting from the race.
      clouds.children.forEach((cloud, i) => {
        cloud.position.x = cloud.userData.baseX + Math.sin(time * 0.02 + i) * 20;
      });
    },
  };
}

function buildSkyDome(center) {
  const material = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
    uniforms: {
      top: { value: new THREE.Color(COLORS.skyTop) },
      horizon: { value: new THREE.Color(COLORS.skyHorizon) },
    },
    vertexShader: /* glsl */ `
      varying float vHeight;
      void main() {
        vHeight = normalize(position).y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 top;
      uniform vec3 horizon;
      varying float vHeight;
      void main() {
        // Banded gradient (posterized) for the retro look instead of a smooth fade.
        float t = clamp(vHeight * 2.2, 0.0, 1.0);
        t = floor(t * 7.0) / 7.0;
        gl_FragColor = vec4(mix(horizon, top, t), 1.0);
      }`,
  });
  const dome = new THREE.Mesh(new THREE.SphereGeometry(900, 24, 16), material);
  dome.position.copy(center);
  dome.renderOrder = -1;
  return dome;
}

function buildSun(center) {
  const sun = new THREE.DirectionalLight(0xfff4e0, 1.6);
  sun.position.set(center.x + 120, 180, center.z + 90);
  sun.target.position.copy(center);
  sun.castShadow = true;
  sun.shadow.mapSize.set(RENDER.shadowMapSize, RENDER.shadowMapSize);
  const extent = 110;
  Object.assign(sun.shadow.camera, { left: -extent, right: extent, top: extent, bottom: -extent });
  sun.shadow.camera.near = 10;
  sun.shadow.camera.far = 500;
  sun.shadow.bias = -0.0008;
  return sun;
}

function buildHills(center) {
  const group = new THREE.Group();
  for (let i = 0; i < HILL_COUNT; i += 1) {
    const angle = (i / HILL_COUNT) * Math.PI * 2 + Math.sin(i * 7.3) * 0.08;
    const distance = 360 + (i % 3) * 70;
    const radius = 70 + ((i * 37) % 60);
    const height = 40 + ((i * 53) % 55);
    const hill = new THREE.Mesh(
      new THREE.ConeGeometry(radius, height, 7, 1),
      new THREE.MeshLambertMaterial({ color: COLORS.hills[i % COLORS.hills.length], flatShading: true }),
    );
    hill.position.set(
      center.x + Math.cos(angle) * distance,
      height / 2 - 4,
      center.z + Math.sin(angle) * distance,
    );
    hill.rotation.y = i;
    group.add(hill);
  }
  return group;
}

function buildClouds(center) {
  const group = new THREE.Group();
  const texture = cloudTexture();
  for (let i = 0; i < CLOUD_COUNT; i += 1) {
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: texture, fog: false, transparent: true, depthWrite: false }),
    );
    const angle = (i / CLOUD_COUNT) * Math.PI * 2;
    const distance = 260 + ((i * 61) % 240);
    sprite.position.set(
      center.x + Math.cos(angle) * distance,
      120 + ((i * 29) % 90),
      center.z + Math.sin(angle) * distance,
    );
    const scale = 70 + ((i * 17) % 60);
    sprite.scale.set(scale, scale * 0.38, 1);
    sprite.userData.baseX = sprite.position.x;
    group.add(sprite);
  }
  return group;
}
