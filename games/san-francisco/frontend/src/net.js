/**
 * Server connection: a Server-Sent Events stream for race events plus race controls.
 * EventSource reconnects by itself; the server replays the current race on connect.
 */

const EVENTS_URL = '/api/events';

/** Streams race events from the game server. */
export class EventStream {
  /**
   * @param {(event: object) => void} onEvent Called once per race event, in order.
   * @param {(connected: boolean) => void} onStatus Called when the connection opens or drops.
   */
  constructor(onEvent, onStatus) {
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.source = null;
  }

  /** Open the stream (safe to call once). */
  connect() {
    this.source = new EventSource(EVENTS_URL);
    this.source.onopen = () => this.onStatus(true);
    this.source.onerror = () => this.onStatus(false);
    this.source.onmessage = (message) => {
      try {
        this.onEvent(JSON.parse(message.data));
      } catch (error) {
        // One malformed event must not stop the race from rendering.
        console.error('Bad race event', error, message.data);
      }
    };
  }
}

/**
 * Ask the server to start a new race.
 * @param {{seed?: number, profile?: string, game?: string, area?: number}} [options]
 * @returns {Promise<{race_id?: string, error?: string}>}
 */
export async function startRace(options = {}) {
  const response = await fetch('/api/race/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(options),
  });
  return response.json();
}

/**
 * Show a game's maze and racers on the attract screen (stops a running race).
 * @param {{profile?: string, seed?: number|null, game?: string, area?: number}} [options]
 *   ``seed`` null or omitted: the game's default maze.
 */
export async function previewRace(options = {}) {
  const body = Object.fromEntries(Object.entries(options).filter(([, value]) => value != null));
  const response = await fetch('/api/race/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return response.json();
}

/**
 * Send the human player's move.
 * @param {string} racer Racer id.
 * @param {'left'|'straight'|'right'|'back'} move
 * @returns {Promise<{accepted: boolean}>}
 */
export async function sendHumanMove(racer, move) {
  const response = await fetch('/api/human/move', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ racer, move }),
  });
  return response.json();
}

/** Ask the server to stop the current race. */
export async function stopRace() {
  await fetch('/api/race/stop', { method: 'POST' });
}
