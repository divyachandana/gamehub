/**
 * Behavior tracker: turns raw clicks, keys and mouse movement into the 10 features the
 * frustration model reads, and sends them to the local server once per second.
 *
 * Only counts leave this file (never what the user typed), and they only go to
 * localhost. FEATURE_NAMES must match server/features.py exactly.
 */
const FEATURE_NAMES = [
  'clicks_per_s', 'rage_clicks', 'dead_clicks', 'idle_s', 'field_reedits',
  'backspaces', 'errors_shown', 'nav_back', 'mouse_thrash', 'time_on_step_s',
];
const WINDOW_MS = 10_000;
const RAGE_CLICKS = 3, RAGE_MS = 1000, RAGE_PX = 30;
const CONTROL = 'input, select, textarea, button, a, label, [data-clickable]';

export class Tracker {
  /** @param {(reply: {score: number, help: ?{message: string, target: ?string}}) => void} onReply */
  constructor(onReply) {
    this.onReply = onReply;
    this.session = null;
    this.reset();
    this._listen();
  }

  reset() {
    const now = Date.now();
    this.clicks = []; this.rage = []; this.dead = []; this.keys = [];
    this.errors = []; this.backs = []; this.reversals = [];
    this.edits = new Map(); this.lastInput = now; this.stepStart = now;
    this.step = 1; this.field = null; this.lastError = null; this.lastErrorAt = 0;
    this.lastDx = 0; this.lastX = null;
  }

  async start(assist) {
    this.reset();
    const r = await fetch('/api/session/start', post({ assist }));
    this.session = (await r.json()).session;
    this.timer = setInterval(() => this._send(), 1000);
  }

  async end(completed) {
    clearInterval(this.timer);
    if (!this.session) return {};
    const r = await fetch('/api/session/end', post({ session: this.session, completed }));
    this.session = null;
    return r.json();
  }

  /** The portal reports what it shows and where the user is. */
  error(id) { this.errors.push(Date.now()); this.lastError = id; this.lastErrorAt = Date.now(); }
  stepChanged(step, wentBack) {
    if (wentBack) this.backs.push(Date.now());
    this.step = step; this.stepStart = Date.now(); this.edits.clear();
  }
  label() { if (this.session) fetch('/api/label', post({ session: this.session, frustrated: true })); }

  features() {
    const now = Date.now();
    const recent = (list) => list.filter((t) => now - t <= WINDOW_MS).length;
    const values = {
      clicks_per_s: this.clicks.filter((c) => now - c.t <= WINDOW_MS).length / (WINDOW_MS / 1000),
      rage_clicks: recent(this.rage),
      dead_clicks: recent(this.dead),
      idle_s: Math.min((now - this.lastInput) / 1000, 30),
      field_reedits: [...this.edits.values()].filter((n) => n > 2).length,
      backspaces: recent(this.keys),
      errors_shown: recent(this.errors),
      nav_back: recent(this.backs),
      mouse_thrash: recent(this.reversals) / (WINDOW_MS / 1000),
      time_on_step_s: Math.min((now - this.stepStart) / 1000, 300),
    };
    return Object.fromEntries(FEATURE_NAMES.map((k) => [k, Math.round(values[k] * 100) / 100]));
  }

  async _send() {
    if (!this.session) return;
    const context = {
      step: this.step,
      field: this.field,
      error: Date.now() - this.lastErrorAt <= WINDOW_MS ? this.lastError : null,
    };
    try {
      const r = await fetch('/api/features', post({ session: this.session, features: this.features(), context }));
      this.onReply(await r.json());
    } catch (e) {
      console.warn('server unreachable', e);
    }
  }

  _listen() {
    document.addEventListener('click', (e) => {
      const now = Date.now();
      this.lastInput = now;
      this.clicks.push({ t: now, x: e.clientX, y: e.clientY });
      this.clicks = this.clicks.filter((c) => now - c.t <= WINDOW_MS);
      const near = this.clicks.filter(
        (c) => now - c.t <= RAGE_MS && Math.hypot(c.x - e.clientX, c.y - e.clientY) <= RAGE_PX,
      );
      if (near.length === RAGE_CLICKS) this.rage.push(now);
      if (!e.target.closest(CONTROL)) this.dead.push(now);
    }, true);

    document.addEventListener('keydown', (e) => {
      this.lastInput = Date.now();
      if (e.key === 'Backspace' || e.key === 'Delete') this.keys.push(Date.now());
      if (e.key === 'F8') { e.preventDefault(); this.label(); }
    }, true);

    // An "edit" is one focus visit that changed the field, not one keystroke, so a field
    // counts as re-edited only when the user keeps coming back to fix it.
    this.editedThisVisit = new Set();
    document.addEventListener('input', (e) => {
      const id = e.target.id;
      if (!id || this.editedThisVisit.has(id)) return;
      this.editedThisVisit.add(id);
      this.edits.set(id, (this.edits.get(id) || 0) + 1);
    }, true);
    document.addEventListener('focusin', (e) => {
      if (!e.target.id || !e.target.matches('input, select, textarea')) return;
      this.editedThisVisit.delete(e.target.id);
      this.field = e.target.dataset.field || e.target.id;
    }, true);

    document.addEventListener('mousemove', (e) => {
      this.lastInput = Date.now();
      if (this.lastX !== null) {
        const dx = e.clientX - this.lastX;
        if (Math.abs(dx) > 4) {
          if (this.lastDx && Math.sign(dx) !== Math.sign(this.lastDx)) this.reversals.push(Date.now());
          this.lastDx = dx;
        }
      }
      this.lastX = e.clientX;
    }, { passive: true });
  }
}

function post(body) {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}
