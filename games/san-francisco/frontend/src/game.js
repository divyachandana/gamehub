/**
 * Game: turns the server's race events into the 3D scene, HUD and sound.
 *
 * The frontend never decides anything — it renders what the server says. Time comes
 * from event timestamps (``t``), so animations stay in sync with the server even after
 * a reconnect replays the race history.
 */

import * as THREE from 'three';
import { CAMERA, HUD, HUMAN_KEYS, LAYERS, MAP_VIEW, STALE_EVENT_S } from './config.js';
import { Car } from './cars/car.js';
import { OptionArrows } from './cars/option-arrows.js';
import { Leaderboard } from './hud/leaderboard.js';
import { Overlays } from './hud/overlays.js';
import { RacerHud, ordinal } from './hud/racer-hud.js';
import { OverviewMarkers } from './render/overview-markers.js';
import { StreetMap } from './render/street-map.js';
import { SplitViews } from './render/views.js';
import { GeoFrame } from './world/geo.js';
import { buildMapWorld, realJunctionLines } from './world/map-world.js';
import { buildMaze } from './world/maze-builder.js';
import { buildScenery } from './world/scenery.js';
import { junctionToWorld, setJunctionLines } from './geometry.js';

const LANE_OFFSETS = [-2.4, 0, 2.4, 1.2];
const PANE_PAN = [-0.6, 0.6, -0.6, 0.6];
const PODIUM_DELAY_MS = 1600;

export class Game {
  /**
   * @param {{canvas: HTMLCanvasElement, panes: HTMLElement[], overlay: HTMLElement,
   *          timer: HTMLElement, matchup: HTMLElement}} dom
   * @param {import('./audio/sound.js').Sound} sound
   */
  constructor(dom, sound) {
    this.dom = dom;
    this.sound = sound;
    this.views = new SplitViews(dom.canvas, dom.panes);
    this.overlays = new Overlays(dom.overlay);
    this.leaderboard = new Leaderboard(dom.panes[dom.panes.length - 1]);
    this.streetMap = new StreetMap();

    this.scene = null;
    this.race = null; // per-race state, rebuilt on every race_init
    this.clockOffset = Infinity;
    this.clock = new THREE.Clock();
    this.overlays.showTitle();
    requestAnimationFrame(() => this.frame());
  }

  /** Current race time in seconds, on the server's clock. */
  raceTime() {
    return Number.isFinite(this.clockOffset) ? performance.now() / 1000 - this.clockOffset : 0;
  }

  /**
   * Handle one server event.
   * @param {object} event
   */
  handle(event) {
    if (event.type === 'race_init') {
      this.init(event);
    }
    if (!this.race) return;
    // The smallest observed (arrival - t) is the true clock offset; replayed history
    // arrives late, so it can only raise the estimate, never fool it.
    this.clockOffset = Math.min(this.clockOffset, performance.now() / 1000 - event.t);
    const live = this.raceTime() - event.t < STALE_EVENT_S;
    const handler = this.handlers[event.type];
    if (handler) handler.call(this, event, live);
  }

  /** Connection state from the event stream. @param {boolean} connected */
  setConnected(connected) {
    document.body.classList.toggle('offline', !connected);
    if (!connected && !this.race) this.overlays.showMessage('CONNECTING TO GAME SERVER…');
    else if (connected && !this.race) this.overlays.showTitle();
  }

  // ------------------------------------------------------------------ race setup

  init(event) {
    this.disposeRace();
    this.clockOffset = Infinity;
    const scene = new THREE.Scene();
    const city = event.maze.city ?? null;
    // The real map is the default view of a street map; ?view=city shows the pixel city.
    const mapView = Boolean(city?.tile_zooms?.length) && new URLSearchParams(location.search).get('view') !== 'city';
    setJunctionLines(...(mapView ? realJunctionLines(city) : [null, null]));
    const geo = new GeoFrame(city, event.maze, { squeeze: !mapView });
    const maze = mapView ? buildMapWorld(event.maze, geo) : buildMaze(event.maze);
    scene.add(maze.group);
    const center = junctionToWorld([(event.maze.width - 1) / 2, (event.maze.height - 1) / 2]);
    const scenery = buildScenery(scene, center, geo, city, mapView);
    this.views.configure(mapView ? MAP_VIEW.camera : CAMERA, !mapView);

    const markers = new OverviewMarkers(scene);
    const racers = new Map();
    event.racers.forEach((racer, index) => {
      const car = new Car(racer, event.maze.start, LANE_OFFSETS[index]);
      scene.add(car.object);
      const arrows = new OptionArrows(scene, racer.color, LAYERS.racerBase + index);
      const hud = new RacerHud(this.dom.panes[index], racer);
      markers.addRacer(racer.id, racer.color, LANE_OFFSETS[index] * 0.6);
      racers.set(racer.id, { racer, index, car, arrows, hud, heading: racer.heading, stats: null });
    });

    // Fewer racers than car panes: drop the spare panes and let the map take the free row.
    const carPanes = this.dom.panes.slice(0, -1);
    carPanes.forEach((pane, i) => pane.classList.toggle('unused', i >= event.racers.length));
    carPanes[0].parentElement.classList.toggle('duo', event.racers.length <= 2);

    this.scene = scene;
    this.views.setup(event.racers.length, event.maze);
    this.leaderboard.reset(event.racers, city);
    // A real street map shows as a real map; a random grid keeps the 3D top-down view.
    this.streetMap.setup(this.dom.panes[this.dom.panes.length - 1], event.maze, geo);
    this.views.streetMapActive = this.streetMap.active;
    this.dom.matchup.textContent = event.racers.map((r) => r.label).join(' VS ');
    this.race = {
      id: event.race_id,
      init: event,
      maze,
      scenery,
      markers,
      racers,
      cars: new Map([...racers].map(([id, entry]) => [id, entry.car])),
      startTime: null,
      endTime: null,
      humanTurn: null, // {racerId, moves, startedAt} while the player must choose
      humanIds: event.racers.filter((r) => r.runtime === 'human').map((r) => r.id),
    };
    this.updateMapLock();
    this.overlays.place = city?.title ?? null;
    if (event.preview) this.overlays.showTitle();
    else this.overlays.showMessage('GET READY');
  }

  disposeRace() {
    if (!this.race) return;
    for (const { index } of this.race.racers.values()) this.sound.stopThinking(index);
    this.sound.stopEngines();
    this.scene?.traverse((object) => {
      object.geometry?.dispose?.();
    });
    this.race = null;
    this.scene = null;
  }

  // ------------------------------------------------------------------ event handlers

  handlers = {
    countdown(event, live) {
      if (!live) return;
      this.overlays.showCountdown(event.remaining);
      this.sound.countdown(false);
    },

    race_start(event, live) {
      this.race.startTime = event.t;
      for (const { hud } of this.race.racers.values()) hud.setStatus('driving', 'GO!');
      if (live) {
        this.overlays.showCountdown('GO!');
        this.sound.countdown(true);
        this.sound.startEngines(this.race.init.racers.map((_, i) => PANE_PAN[i]));
      } else {
        this.overlays.hide();
      }
    },

    thinking(event, live) {
      const entry = this.race.racers.get(event.racer);
      entry.car.setThinking(true);
      entry.arrows.show(event.node, entry.heading, event.options);
      if (entry.racer.runtime === 'human') {
        this.race.humanTurn = { racerId: event.racer, moves: event.options, startedAt: performance.now() };
        entry.hud.setStatus('thinking', 'YOUR MOVE!');
        entry.hud.showBanner(`Your move · ${arrowHint(event.options)}`, 'warn');
      } else {
        entry.hud.setStatus('thinking', 'THINKING…');
        entry.hud.showBanner(`${this.junctionName(event.node)} · thinking…`, 'info');
      }
      entry.hud.setTps(0);
      entry.hud.setTtft(null);
      if (live) this.sound.startThinking(entry.index);
    },

    tokens(event) {
      const entry = this.race.racers.get(event.racer);
      entry.hud.setTps(event.tps);
      entry.hud.setTokens(event.tokens_decision, event.tokens_total);
      entry.hud.setTtft(event.ttft_ms);
    },

    decision(event, live) {
      const entry = this.race.racers.get(event.racer);
      entry.stats = event.stats;
      entry.arrows.choose(event.move);
      entry.hud.setTps(event.metrics.tps);
      entry.hud.setTokens(event.metrics.tokens, event.stats.tokens_total);
      entry.hud.setTtft(event.metrics.ttft_ms);
      entry.hud.showBubble(event.move, event.why);
      if (entry.racer.runtime === 'human') {
        entry.hud.setReaction(event.metrics.latency_ms);
        entry.hud.setTimer(0);
        if (this.race.humanTurn?.racerId === event.racer) this.race.humanTurn = null;
      }
      entry.hud.showBanner(
        event.optimal ? `Turning ${event.move}` : `${event.move}?!`,
        event.optimal ? 'good' : 'warn',
      );
      this.leaderboard.updateStats(event.racer, event.stats);
      this.leaderboard.addDecision(entry.racer, event);
      if (live) {
        this.sound.stopThinking(entry.index);
        this.sound.decision(entry.index, event.optimal);
      }
    },

    drive(event) {
      const entry = this.race.racers.get(event.racer);
      const uturn = event.heading !== entry.heading && isOpposite(event.heading, entry.heading);
      entry.car.startDrive(event, uturn);
      entry.car.setDrone(false); // the drone found the road: recall it
      entry.heading = event.heading;
      entry.hud.setStatus('driving', 'DRIVING');
      entry.hud.setProgress(event.progress);
      this.race.markers.addTrail(event.racer, event.src, event.dst);
      this.streetMap.addTrail(entry.racer, entry.index, event.src, event.dst);
      this.leaderboard.setProgress(event.racer, event.progress);
      // Keep the chosen arrow visible briefly as the car pulls away, then clear.
      setTimeout(() => entry.arrows.clear(), 450);
    },

    cheat(event, live) {
      const entry = this.race.racers.get(event.racer);
      entry.stats = event.stats;
      const percent = Math.round(event.probability * 100);
      const what = event.kind === 'jump' ? 'HEDGE JUMP' : 'DRONE';
      if (event.used) {
        const left = event.kind === 'jump' ? event.jumps_left : event.drones_left;
        entry.hud.showBanner(`${what}! LAYA ${percent}% · ${left} LEFT`, 'bad');
        entry.hud.setStatus('thinking', event.kind === 'jump' ? 'JUMPING!' : 'DRONE SCOUTING…');
        if (event.kind === 'drone') entry.car.setDrone(true);
      } else {
        entry.hud.showBanner(`LAYA: NO ${what.toLowerCase()} (${percent}%)`, 'info');
      }
      this.leaderboard.updateStats(event.racer, event.stats);
      this.leaderboard.addCheat(entry.racer, event);
      if (live && event.used) this.sound.cheat();
    },

    incident(event, live) {
      const entry = this.race.racers.get(event.racer);
      const kind = ['dead_end', 'invalid_move', 'offline', 'timeout'].includes(event.kind) ? 'bad' : 'warn';
      entry.hud.showBanner(event.text, kind);
      if (!live) return;
      if (event.kind === 'wrong_turn') this.sound.wrongTurn();
      else if (event.kind === 'dead_end') this.sound.deadEnd();
      else if (event.kind === 'uturn') this.sound.uturn();
    },

    finish(event, live) {
      const entry = this.race.racers.get(event.racer);
      entry.stats = event.stats;
      entry.car.setFinished();
      entry.hud.setStatus('finished', `FINISHED ${ordinal(event.place).toUpperCase()}`);
      entry.hud.setPlace(event.place);
      entry.hud.showBanner(`Finish! ${event.time_s.toFixed(1)}s`, 'good');
      entry.hud.setProgress(1);
      this.leaderboard.updateStats(event.racer, event.stats);
      this.leaderboard.setFinished(event.racer, event.place);
      this.updateMapLock();
      if (live) this.sound.finish(event.place);
    },

    race_end(event, live) {
      this.race.endTime = event.t;
      this.race.humanTurn = null;
      this.updateMapLock();
      this.sound.stopEngines();
      const raceId = this.race.id;
      const show = () => {
        if (this.race?.id !== raceId) return; // a new race started meanwhile
        this.overlays.showPodium(
          this.race.init.racers,
          event.results,
          this.race.init.maze.optimal_path.length - 1,
        );
      };
      if (live) setTimeout(show, PODIUM_DELAY_MS);
      else show();
    },
  };

  // ------------------------------------------------------------------ frame loop

  frame() {
    requestAnimationFrame(() => this.frame());
    const dt = Math.min(this.clock.getDelta(), 0.1);
    const wallTime = this.clock.elapsedTime;
    if (!this.race) return;

    const raceTime = this.raceTime();
    const cars = [...this.race.racers.values()].map((entry) => entry.car);
    for (const entry of this.race.racers.values()) {
      entry.car.update(raceTime, dt, wallTime);
      entry.arrows.update(wallTime, dt);
      this.sound.setEngine(entry.index, entry.car.speed, entry.car.finished);
    }
    this.updateHumanTimer();
    this.race.maze.update(wallTime);
    this.race.scenery.update(wallTime);
    this.race.markers.update(this.race.cars, wallTime);
    this.views.updateCameras(cars, dt, this.race.maze.isOpen);
    this.views.render(this.scene);
    this.streetMap.draw([...this.race.racers.values()], wallTime);
    this.updateTimer(raceTime);
  }

  updateTimer(raceTime) {
    const { startTime, endTime } = this.race;
    if (startTime == null) {
      this.dom.timer.textContent = '00:00.0';
      return;
    }
    const elapsed = Math.max(0, (endTime ?? raceTime) - startTime);
    const minutes = Math.floor(elapsed / 60);
    const seconds = (elapsed % 60).toFixed(1).padStart(4, '0');
    this.dom.timer.textContent = `${String(minutes).padStart(2, '0')}:${seconds}`;
  }

  /**
   * Translate an arrow key into the human player's move, if it is their turn and legal.
   * @param {string} key KeyboardEvent.key
   * @returns {{racer: string, move: string}|null}
   */
  humanMove(key) {
    const turn = this.race?.humanTurn;
    const move = HUMAN_KEYS[key];
    if (!turn || !move || !turn.moves.includes(move)) return null;
    return { racer: turn.racerId, move };
  }

  /** Drain the player's red bar while they decide. */
  updateHumanTimer() {
    const turn = this.race.humanTurn;
    if (!turn) return;
    const elapsed = (performance.now() - turn.startedAt) / 1000;
    this.race.racers.get(turn.racerId).hud.setTimer(1 - elapsed / HUD.humanTimeoutS);
  }

  /**
   * In play mode the overview map stays hidden until every human has finished, so the
   * player gets no more information than the models (they only know the exit's direction).
   */
  updateMapLock() {
    const { humanIds, racers, endTime } = this.race;
    const humanStillDriving = humanIds.some((id) => !racers.get(id).car.finished);
    const locked = humanIds.length > 0 && humanStillDriving && endTime == null;
    this.views.mapLocked = locked;
    this.leaderboard.setMapLocked(locked);
    this.streetMap.setLocked(locked);
  }

  /**
   * Real cross streets of a junction, e.g. "Union & Stockton" (just "Junction" on a random grid).
   * @param {[number, number]} node
   */
  junctionName([x, y]) {
    const city = this.race.init.maze.city;
    if (!city) return 'Junction';
    return `${shortStreet(city.rows[y].name)} & ${shortStreet(city.columns[x].name)}`;
  }

  /** Expand one racer's pane to full screen (or ``null`` for the split view). @param {number|null} index */
  focus(index) {
    if (index !== null && this.dom.panes[index].classList.contains('unused')) return;
    this.dom.panes.forEach((pane, i) => {
      pane.hidden = index !== null && i !== index;
    });
    this.dom.panes[0].parentElement.classList.toggle('focused', index !== null);
  }

  toggleRetro() {
    return this.views.toggleRetro();
  }
}

const STREET_TYPES = ['street', 'st', 'avenue', 'ave', 'boulevard', 'blvd', 'road', 'rd', 'place', 'way'];

/** "Union Street" -> "Union": banners are narrow. */
function shortStreet(name) {
  const words = name.split(' ');
  const last = words[words.length - 1].toLowerCase().replace('.', '');
  return words.length > 1 && STREET_TYPES.includes(last) ? words.slice(0, -1).join(' ') : name;
}

function isOpposite(a, b) {
  return (
    (a === 'N' && b === 'S') || (a === 'S' && b === 'N') || (a === 'E' && b === 'W') || (a === 'W' && b === 'E')
  );
}

// ASCII on purpose: the pixel font has no arrow glyphs.
const ARROWS = { left: '<', straight: '^', right: '>', back: 'v' };

/** e.g. "< >" for the moves available at a junction (U-turn not advertised). */
function arrowHint(moves) {
  return moves
    .filter((move) => move !== 'back')
    .map((move) => ARROWS[move])
    .join(' ');
}
