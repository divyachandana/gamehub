/**
 * Chiptune sound effects synthesized with the Web Audio API — no audio files, so
 * nothing to download or license. Each racer gets its own engine voice (panned to its
 * pane's side) and its own chime pitch, so you can hear who decided first.
 *
 * Browsers only allow audio after a user gesture; call ``unlock()`` from a key/click.
 */

const MASTER_VOLUME = 0.32;
const ENGINE_VOLUME = 0.05;
const RACER_PITCH = [1, 1.26, 1.5, 1.68]; // chime pitch per racer (major-ish intervals)

export class Sound {
  constructor() {
    this.ctx = null;
    this.master = null;
    this.muted = false;
    this.engines = [];
    this.tickTimers = new Map();
  }

  /** Create the audio context (must run inside a user gesture). */
  unlock() {
    if (this.ctx) {
      if (this.ctx.state === 'suspended') this.ctx.resume();
      return;
    }
    this.ctx = new AudioContext();
    this.master = this.ctx.createGain();
    this.master.gain.value = this.muted ? 0 : MASTER_VOLUME;
    this.master.connect(this.ctx.destination);
  }

  /** @returns {boolean} new muted state */
  toggleMute() {
    this.muted = !this.muted;
    if (this.master) this.master.gain.value = this.muted ? 0 : MASTER_VOLUME;
    return this.muted;
  }

  get ready() {
    return Boolean(this.ctx);
  }

  // ------------------------------------------------------------------ engines

  /** @param {number[]} pans Stereo position per racer, -1 (left) .. 1 (right). */
  startEngines(pans) {
    this.stopEngines();
    if (!this.ready) return;
    this.engines = pans.map((pan) => {
      const osc = this.ctx.createOscillator();
      osc.type = 'sawtooth';
      const filter = this.ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = 420;
      const gain = this.ctx.createGain();
      gain.gain.value = 0;
      const panner = this.ctx.createStereoPanner();
      panner.pan.value = pan;
      osc.connect(filter).connect(gain).connect(panner).connect(this.master);
      osc.start();
      return { osc, gain, filter };
    });
  }

  stopEngines() {
    for (const engine of this.engines) engine.osc.stop();
    this.engines = [];
  }

  /**
   * Engine pitch follows car speed.
   * @param {number} index Racer index.
   * @param {number} speed World units per second.
   * @param {boolean} finished
   */
  setEngine(index, speed, finished) {
    const engine = this.engines[index];
    if (!engine) return;
    const now = this.ctx.currentTime;
    const rev = Math.min(1, speed / 22);
    engine.osc.frequency.setTargetAtTime(48 + rev * 90, now, 0.08);
    engine.filter.frequency.setTargetAtTime(300 + rev * 900, now, 0.1);
    engine.gain.gain.setTargetAtTime(finished ? 0 : ENGINE_VOLUME * (0.45 + rev), now, 0.1);
  }

  // ------------------------------------------------------------------ one-shots

  /** Countdown beep: low for 3-2-1, high for GO. @param {boolean} go */
  countdown(go) {
    this.tone({ freq: go ? 880 : 440, duration: go ? 0.5 : 0.18, type: 'square', volume: 0.5 });
  }

  /** Repeating soft tick while a model thinks. @param {number} index */
  startThinking(index) {
    this.stopThinking(index);
    if (!this.ready) return;
    const tick = () => this.tone({ freq: 1400 * RACER_PITCH[index % 4], duration: 0.03, type: 'square', volume: 0.12 });
    tick();
    this.tickTimers.set(index, setInterval(tick, 220));
  }

  /** @param {number} index */
  stopThinking(index) {
    clearInterval(this.tickTimers.get(index));
    this.tickTimers.delete(index);
  }

  /** Two-note chime when a model answers. @param {number} index @param {boolean} good */
  decision(index, good) {
    const base = 523 * RACER_PITCH[index % 4];
    this.tone({ freq: base, duration: 0.09, type: 'triangle', volume: 0.45 });
    this.tone({ freq: good ? base * 1.5 : base * 0.75, duration: 0.14, type: 'triangle', volume: 0.45, delay: 0.08 });
  }

  /** A rogue cheats: rising zap. */
  cheat() {
    this.tone({ freq: 220, duration: 0.3, type: 'sawtooth', volume: 0.35, slideTo: 880 });
    this.noise(0.2, 0.2, 3000);
  }

  wrongTurn() {
    this.tone({ freq: 160, duration: 0.32, type: 'square', volume: 0.35, slideTo: 90 });
  }

  deadEnd() {
    this.noise(0.35, 0.4);
    this.tone({ freq: 110, duration: 0.4, type: 'sawtooth', volume: 0.3, slideTo: 50 });
  }

  uturn() {
    this.noise(0.45, 0.15, 2400);
  }

  /** Short arpeggio when a racer finishes; full fanfare for the winner. @param {number} place */
  finish(place) {
    const notes = place === 1 ? [523, 659, 784, 1047, 784, 1047] : [392, 523, 659];
    notes.forEach((freq, i) => this.tone({ freq, duration: 0.14, type: 'square', volume: 0.35, delay: i * 0.12 }));
  }

  // ------------------------------------------------------------------ primitives

  tone({ freq, duration, type = 'square', volume = 0.3, delay = 0, slideTo = null }) {
    if (!this.ready) return;
    const start = this.ctx.currentTime + delay;
    const osc = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, start);
    if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, start + duration);
    gain.gain.setValueAtTime(volume, start);
    gain.gain.exponentialRampToValueAtTime(0.001, start + duration);
    osc.connect(gain).connect(this.master);
    osc.start(start);
    osc.stop(start + duration + 0.02);
  }

  noise(duration, volume, lowpass = 900) {
    if (!this.ready) return;
    const length = Math.floor(this.ctx.sampleRate * duration);
    const buffer = this.ctx.createBuffer(1, length, this.ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) data[i] = (Math.random() * 2 - 1) * (1 - i / length);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    const filter = this.ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = lowpass;
    const gain = this.ctx.createGain();
    gain.gain.value = volume;
    source.connect(filter).connect(gain).connect(this.master);
    source.start();
  }
}
