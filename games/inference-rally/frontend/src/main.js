/**
 * Entry point: wires the DOM, the event stream, keyboard controls and the game.
 *
 * Keys: Enter = start / rematch · S = stop · N = new maze · G = demo/play/unfair mode · M = mute
 *       H = back to the game hub
 *       P = retro pixels on/off · 1-3 = focus one racer · 0 / Esc = split screen
 *       Arrows = drive (play mode: ← left, ↑ straight, → right, ↓ U-turn)
 */

import '@fontsource/press-start-2p';
import './styles.css';
import { Sound } from './audio/sound.js';
import { HUB_PORT, MODE_CYCLE, MODES } from './config.js';
import { Game } from './game.js';
import { EventStream, previewRace, sendHumanMove, startRace } from './net.js';

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

/** Reflect the current mode on the button; the server's lineup is the source of truth. */
function showMode(next) {
  mode = next;
  const button = document.querySelector('#btn-mode');
  button.textContent = `MODE: ${MODES[mode].label}`;
  button.classList.toggle('play', mode === 'play');
  button.classList.toggle('unfair', mode === 'unfair');
}

const stream = new EventStream(
  (event) => {
    if (event.type === 'race_init') {
      currentSeed = event.maze.seed;
      showMode(modeOf(event.racers));
    }
    game.handle(event);
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

async function launch(seed) {
  sound.unlock(); // a key press is the user gesture browsers require for audio
  const options = { profile: MODES[mode].profile };
  if (seed != null) options.seed = seed;
  const result = await startRace(options);
  if (result.error) dom.status.textContent = result.error;
}

const actions = {
  Enter: () => launch(currentSeed),
  n: () => launch(Math.floor(Math.random() * 100000)),
  // Stop the race: back to the title screen on the same maze, ready to restart.
  s: async () => {
    const result = await previewRace(MODES[mode].profile, currentSeed);
    if (result.error) dom.status.textContent = result.error;
  },
  g: async () => {
    const next = MODE_CYCLE[(MODE_CYCLE.indexOf(mode) + 1) % MODE_CYCLE.length];
    showMode(next);
    const result = await previewRace(MODES[next].profile);
    if (result.error) dom.status.textContent = result.error;
  },
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
document.querySelector('#btn-sound').addEventListener('click', actions.m);
document.querySelector('#btn-retro').addEventListener('click', actions.p);
