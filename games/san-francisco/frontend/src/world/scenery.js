/**
 * Everything outside the maze: sky gradient, fog, lights, clouds and the San Francisco
 * bay area (ground, water, hills, landmarks).
 */

import * as THREE from 'three';
import { COLORS, MAP_VIEW, RENDER } from '../config.js';
import { buildBayArea } from './bay-area.js';
import { cloudTexture } from './textures.js';

const CLOUD_COUNT = 16;

/**
 * Add sky, fog, lights, the bay area and clouds to ``scene``.
 * @param {THREE.Scene} scene
 * @param {THREE.Vector3} center World center of the maze (scenery is placed around it).
 * @param {import('./geo.js').GeoFrame} geo Places real latitude/longitude in the world.
 * @param {object|null} city Street map, or null for a random grid.
 * @param {boolean} [mapView] The real map: no modelled bay area, fog farther out.
 * @returns {{sun: THREE.DirectionalLight, update: (time: number) => void}}
 */
export function buildScenery(scene, center, geo, city, mapView = false) {
  const [fogNear, fogFar] = mapView ? [MAP_VIEW.fogNear, MAP_VIEW.fogFar] : [RENDER.fogNear, RENDER.fogFar];
  scene.fog = new THREE.Fog(COLORS.skyHorizon, fogNear, fogFar);
  scene.add(buildSkyDome(center, !mapView));

  const hemi = new THREE.HemisphereLight(0xdfeaff, 0x6e6a60, 1.15);
  scene.add(hemi);
  const sun = buildSun(center);
  scene.add(sun, sun.target);

  const bay = mapView ? null : buildBayArea(center, geo, city);
  if (bay) scene.add(bay.group);
  // Pixel clouds and a banded sky belong to the arcade look, not to the real map.
  const clouds = mapView ? new THREE.Group() : buildClouds(center);
  scene.add(clouds);

  return {
    sun,
    update(time) {
      // Slow drift keeps the sky alive without distracting from the race.
      clouds.children.forEach((cloud, i) => {
        cloud.position.x = cloud.userData.baseX + Math.sin(time * 0.02 + i) * 20;
      });
      bay?.update(time);
    },
  };
}

function buildSkyDome(center, banded) {
  const material = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
    uniforms: {
      top: { value: new THREE.Color(COLORS.skyTop) },
      horizon: { value: new THREE.Color(COLORS.skyHorizon) },
      bands: { value: banded ? 7 : 0 },
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
      uniform float bands;
      varying float vHeight;
      void main() {
        // Banded gradient (posterized) for the retro look; bands = 0 fades smoothly.
        float t = clamp(vHeight * 2.2, 0.0, 1.0);
        if (bands > 0.0) t = floor(t * bands) / bands;
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
