/**
 * Arcade HUD drawn over one racer's chase-cam pane (plain DOM, crisp at any resolution):
 *   left red bar   = live tokens/sec      right teal bar = progress to the exit
 *   top banner     = events               bubble         = the model's "why"
 *   bottom         = tokens, TTFT, device, position
 */

import { HUD } from '../config.js';

const PLACE_SUFFIX = { 1: 'st', 2: 'nd', 3: 'rd' };

/** @param {number} place */
export function ordinal(place) {
  return `${place}${PLACE_SUFFIX[place] ?? 'th'}`;
}

function el(tag, className, parent, text = '') {
  const node = document.createElement(tag);
  node.className = className;
  if (text) node.textContent = text;
  parent.appendChild(node);
  return node;
}

export class RacerHud {
  /**
   * @param {HTMLElement} pane Pane element to draw into.
   * @param {object} racer Racer view (id, name, device, color).
   */
  constructor(pane, racer) {
    this.pane = pane;
    this.racer = racer;
    pane.innerHTML = '';
    pane.style.setProperty('--racer', racer.color);
    const root = el('div', 'hud', pane);

    this.human = racer.runtime === 'human';
    const tps = el('div', 'tps-bar', root);
    this.segments = Array.from({ length: HUD.tpsSegments }, () => el('i', '', tps));
    // For the human player the red bar is the decision timer instead of tokens/sec.
    el('div', 'bar-label', root, this.human ? 'TIME' : 'TOK/S').classList.add('bar-label-left');

    const progress = el('div', 'progress-bar', root);
    this.progressMarker = el('i', 'progress-marker', progress);
    el('div', 'bar-label bar-label-right', root, 'EXIT');

    const header = el('div', 'hud-header', root);
    el('span', 'racer-dot', header);
    el('span', 'racer-name', header, `${racer.name}`);
    el('span', `device-badge device-${racer.device.toLowerCase()}`, header, racer.device);

    this.banner = el('div', 'banner', root);
    this.bubble = el('div', 'bubble', root);
    this.status = el('div', 'status', root, 'READY');

    const bottom = el('div', 'hud-bottom', root);
    this.tpsText = el('span', 'tps-text', bottom, '0.0 TOK/S');
    this.tokensText = el('span', 'tokens-text', bottom, '0 TOK');
    this.ttftText = el('span', 'ttft-text', bottom, 'TTFT --');
    this.placeText = el('span', 'place-text', root, '');

    this.bannerTimer = null;
    this.bubbleTimer = null;
    this.setBar(0);
    if (this.human) {
      this.tpsText.textContent = 'REACTION --';
      this.tokensText.textContent = 'YOU DRIVE';
      this.ttftText.textContent = 'ARROWS < ^ > · v = U-TURN';
    }
  }

  /** Light up the segmented bar. @param {number} tps */
  setTps(tps) {
    if (this.human) return;
    this.setBar(tps / HUD.tpsBarMax);
    this.tpsText.textContent = `${tps.toFixed(1)} TOK/S`;
  }

  /** Human decision timer: 1 = full time left, 0 = out of time. @param {number} fraction */
  setTimer(fraction) {
    this.setBar(fraction);
  }

  /** @param {number} ms Human reaction time for the last decision. */
  setReaction(ms) {
    this.tpsText.textContent = `REACTION ${(ms / 1000).toFixed(1)}S`;
  }

  /** @param {number} fraction 0..1 of the bar to light. */
  setBar(fraction) {
    const lit = Math.round(Math.min(1, Math.max(0, fraction)) * this.segments.length);
    // Segment 0 is the top of the bar, so light from the bottom up.
    this.segments.forEach((segment, index) => {
      segment.classList.toggle('lit', this.segments.length - index <= lit);
    });
  }

  /** @param {number} decisionTokens @param {number} totalTokens */
  setTokens(decisionTokens, totalTokens) {
    if (this.human) return;
    this.tokensText.textContent = `${decisionTokens} / ${totalTokens} TOK`;
  }

  /** @param {number|null} ttftMs */
  setTtft(ttftMs) {
    if (this.human) return;
    this.ttftText.textContent = ttftMs == null ? 'TTFT --' : `TTFT ${Math.round(ttftMs)}MS`;
  }

  /** @param {number} progress 0..1 */
  setProgress(progress) {
    this.progressMarker.style.bottom = `${Math.round(progress * 100)}%`;
  }

  /** @param {'ready'|'thinking'|'driving'|'finished'} state @param {string} [label] */
  setStatus(state, label) {
    this.pane.dataset.state = state;
    this.status.textContent = label ?? state.toUpperCase();
  }

  /** @param {number} place */
  setPlace(place) {
    this.placeText.textContent = ordinal(place).toUpperCase();
  }

  /**
   * Flash an event across the top of the pane.
   * @param {string} text
   * @param {'info'|'warn'|'bad'|'good'} [kind]
   */
  showBanner(text, kind = 'info') {
    this.banner.textContent = text.toUpperCase();
    this.banner.dataset.kind = kind;
    this.banner.classList.remove('show');
    void this.banner.offsetWidth; // restart the CSS animation
    this.banner.classList.add('show');
    clearTimeout(this.bannerTimer);
    this.bannerTimer = setTimeout(() => this.banner.classList.remove('show'), HUD.bannerMs);
  }

  /** Show the model's reasoning in a speech bubble. @param {string} move @param {string} why */
  showBubble(move, why) {
    this.bubble.innerHTML = '';
    el('b', '', this.bubble, move.toUpperCase());
    if (why) el('span', '', this.bubble, `"${why}"`);
    this.bubble.classList.add('show');
    clearTimeout(this.bubbleTimer);
    this.bubbleTimer = setTimeout(() => this.bubble.classList.remove('show'), HUD.bubbleMs);
  }
}
