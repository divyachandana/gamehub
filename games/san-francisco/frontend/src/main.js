/**
 * Entry point of the San Francisco game page: wires the DOM, the event stream, keyboard
 * controls and the game.
 *
 * Keys: Enter = start / rematch · S = stop · N = new maze · G = demo/play/unfair mode · M = mute
 *       A = next map area · H = back to the game hub
 *       P = retro pixels on/off · 1-3 = focus one racer · 4 = map full screen
 *       0 / Esc = split screen
 *       Arrows = drive (play mode: ← left, ↑ straight, → right, ↓ U-turn)
 */

import '@fontsource/press-start-2p';
import './styles.css';
import { Sound } from './audio/sound.js';
import { GAMES, HUB_PORT, MODE_CYCLE, MODES } from './config.js';
import { Game } from './game.js';
import { EventStream, previewRace, sendHumanMove, startRace } from './net.js';

/** The game this page plays (its server's game key). */
const GAME = 'sf';

const dom = {
  canvas: document.querySelector('#scene'),
  panes: [...document.querySelectorAll('.pane')],
  overlay: document.querySelector('#overlay'),
  timer: document.querySelector('#timer'),
  matchup: document.querySelector('#matchup'),
  status: document.querySelector('#status'),
};

const sound = new Sound();
const game = new Game(dom, sound);
let currentSeed = null;
let mode = 'demo';
let area = 0; // which of the game's maps (San Francisco has three)
let areaCount = 0;
let following = false; // false while the server still shows another game's race
let switching = false;

document.title = GAMES[GAME].title;
document.querySelector('.logo').innerHTML = GAMES[GAME].logo;
game.overlays.logo = GAMES[GAME].titleLogo;

/** Reflect the current mode on the button; the server's lineup is the source of truth. */
function showMode(next) {
  mode = next;
  const button = document.querySelector('#btn-mode');
  button.textContent = `MODE: ${MODES[mode].label}`;
  button.classList.toggle('play', mode === 'play');
  button.classList.toggle('unfair', mode === 'unfair');
}

/** Show the MAP button only for a game with several maps, labelled with the current one. */
function showArea(event) {
  area = event.area ?? 0;
  areaCount = event.areas?.length ?? 0;
  const button = document.querySelector('#btn-map');
  button.hidden = areaCount < 2;
  if (areaCount) button.textContent = `MAP: ${event.areas[area].split(' · ').pop().toUpperCase()}`;
}

/** Ask the server to show this page's game, e.g. when it was still showing the other one. */
async function showOwnGame() {
  if (switching) return;
  switching = true;
  const result = await previewRace({ profile: MODES[mode].profile, game: GAME, area: 0 });
  switching = false;
  if (result.error) dom.status.textContent = result.error;
}

const stream = new EventStream(
  (event) => {
    if (event.type === 'race_init') {
      following = (event.game ?? GAME) === GAME;
      if (!following) {
        showOwnGame();
        return;
      }
      currentSeed = event.maze.seed;
      showMode(modeOf(event.racers));
      showArea(event);
    }
    if (!following) return; // the other game's race events, until ours is announced
    game.handle(event);
    if (event.type === 'race_init') {
      // The map view starts with crisp pixels; keep the button honest.
      document.querySelector('#btn-retro').textContent = game.views.retro ? 'RETRO ON' : 'RETRO OFF';
    }
  },
  (connected) => {
    dom.status.textContent = connected ? 'LIVE' : 'OFFLINE';
    dom.status.classList.toggle('live', connected);
    game.setConnected(connected);
  },
);
stream.connect();

/** Which mode a lineup belongs to: a rogue means UNFAIR, a human means PLAY. */
function modeOf(racers) {
  if (racers.some((racer) => racer.rogue)) return 'unfair';
  return racers.some((racer) => racer.runtime === 'human') ? 'play' : 'demo';
}

/** This page's game and map, plus whatever else a request needs. */
function request(extra = {}) {
  return { profile: MODES[mode].profile, game: GAME, area, ...extra };
}

async function launch(seed) {
  sound.unlock(); // a key press is the user gesture browsers require for audio
  const result = await startRace(request(seed == null ? {} : { seed }));
  if (result.error) dom.status.textContent = result.error;
}

async function preview(options) {
  const result = await previewRace(request(options));
  if (result.error) dom.status.textContent = result.error;
}

const actions = {
  Enter: () => launch(currentSeed),
  n: () => launch(Math.floor(Math.random() * 100000)),
  // Stop the race: back to the title screen on the same maze, ready to restart.
  s: () => preview({ seed: currentSeed }),
  g: () => {
    const next = MODE_CYCLE[(MODE_CYCLE.indexOf(mode) + 1) % MODE_CYCLE.length];
    showMode(next);
    return preview({ profile: MODES[next].profile });
  },
  // Next map area (San Francisco: North Beach -> Alamo Square -> Mission).
  a: () => (areaCount > 1 ? preview({ area: (area + 1) % areaCount }) : undefined),
  h: () => {
    location.href = `${location.protocol}//${location.hostname}:${HUB_PORT}/`;
  },
  m: () => {
    sound.unlock();
    document.querySelector('#btn-sound').textContent = sound.toggleMute() ? 'SOUND OFF' : 'SOUND ON';
  },
  p: () => {
    document.querySelector('#btn-retro').textContent = game.toggleRetro() ? 'RETRO ON' : 'RETRO OFF';
  },
  1: () => game.focus(0),
  2: () => game.focus(1),
  3: () => game.focus(2),
  4: () => game.focus(dom.panes.length - 1),
  0: () => game.focus(null),
  Escape: () => game.focus(null),
};

window.addEventListener('keydown', (event) => {
  if (event.key.startsWith('Arrow')) {
    event.preventDefault(); // arrows drive the car, never scroll the page
    const choice = game.humanMove(event.key);
    if (choice) sendHumanMove(choice.racer, choice.move);
    return;
  }
  const action = actions[event.key] ?? actions[event.key.toLowerCase()];
  if (!action || event.repeat) return;
  event.preventDefault();
  action();
});

document.querySelector('#btn-hub').addEventListener('click', actions.h);
document.querySelector('#btn-start').addEventListener('click', actions.Enter);
document.querySelector('#btn-stop').addEventListener('click', actions.s);
document.querySelector('#btn-new').addEventListener('click', actions.n);
document.querySelector('#btn-mode').addEventListener('click', actions.g);
document.querySelector('#btn-map').addEventListener('click', actions.a);
document.querySelector('#btn-sound').addEventListener('click', actions.m);
document.querySelector('#btn-retro').addEventListener('click', actions.p);
