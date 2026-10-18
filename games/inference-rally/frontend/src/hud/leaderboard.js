/**
 * Overview pane panel: live standings, per-racer metrics and a decision feed.
 * The map itself is rendered by WebGL in the left part of the same pane.
 */

import { HUD } from '../config.js';
import { ordinal } from './racer-hud.js';

function el(tag, className, parent, text = '') {
  const node = document.createElement(tag);
  node.className = className;
  if (text) node.textContent = text;
  parent.appendChild(node);
  return node;
}

export class Leaderboard {
  /** @param {HTMLElement} pane Overview pane element. */
  constructor(pane) {
    this.pane = pane;
    this.rows = new Map();
  }

  /** @param {object[]} racers Racer views from ``race_init``. */
  reset(racers) {
    // Forget the previous lineup too: a racer left over from another mode would still be
    // ranked (invisibly) and push everyone's place down.
    this.rows.clear();
    this.pane.innerHTML = '';
    this.pane.dataset.state = 'overview';
    const panel = el('div', 'board', this.pane);
    el('div', 'board-title', panel, 'STANDINGS');
    this.table = el('div', 'board-rows', panel);
    for (const racer of racers) {
      const row = el('div', 'board-row', this.table);
      row.style.setProperty('--racer', racer.color);
      const place = el('span', 'board-place', row, '-');
      el('span', 'board-name', row, racer.label);
      const tps = el('span', 'board-tps', row, '0.0');
      const extra = el('span', 'board-extra', row, 'TTFT -- · 0 TOK · 0 X');
      if (racer.runtime === 'human') row.classList.add('human');
      this.rows.set(racer.id, { row, place, tps, extra, racer, progress: 0, finishPlace: null });
    }
    el('div', 'board-title board-title-feed', panel, 'DECISIONS');
    this.feed = el('div', 'feed', panel);
    el('div', 'map-legend', this.pane, 'WHITE DASH = A* BEST ROUTE');
    this.lock = el('div', 'map-lock', this.pane);
    el('b', '', this.lock, 'MAP LOCKED');
    el('span', '', this.lock, 'FOLLOW THE GREEN BEAM TO THE EXIT');
    this.setMapLocked(false);
  }

  /** @param {string} racerId @param {object} stats RacerStats from the server. */
  updateStats(racerId, stats) {
    const entry = this.rows.get(racerId);
    if (!entry) return;
    if (entry.racer.runtime === 'human') {
      // Humans have no tokens: show average reaction time instead.
      entry.tps.textContent = `${(stats.avg_ttft_ms / 1000).toFixed(1)}S`;
      entry.extra.textContent = `REACTION TIME · ${stats.wrong_turns} X`;
      return;
    }
    entry.tps.textContent = stats.avg_tps.toFixed(1);
    entry.extra.textContent = `TTFT ${Math.round(stats.avg_ttft_ms)}MS · ${stats.tokens_total} TOK · ${stats.wrong_turns} X`;
  }

  /** @param {string} racerId @param {number} progress */
  setProgress(racerId, progress) {
    const entry = this.rows.get(racerId);
    if (entry) entry.progress = progress;
    this.sort();
  }

  /** @param {string} racerId @param {number} place */
  setFinished(racerId, place) {
    const entry = this.rows.get(racerId);
    if (entry) entry.finishPlace = place;
    this.sort();
  }

  /**
   * Add a line to the decision feed.
   * @param {object} racer
   * @param {object} decision DecisionEvent from the server.
   */
  addDecision(racer, decision) {
    const line = el('div', `feed-line${decision.optimal ? '' : ' bad'}`, this.feed);
    line.style.setProperty('--racer', racer.color);
    el('b', '', line, racer.label);
    el('span', '', line, ` ${decision.move.toUpperCase()} ${(decision.metrics.latency_ms / 1000).toFixed(1)}S`);
    this.feed.prepend(line);
    while (this.feed.children.length > HUD.feedLength) this.feed.lastChild.remove();
  }

  /** A rogue's cheat in the feed: what Laya was asked and whether it said yes. */
  addCheat(racer, cheat) {
    const line = el('div', `feed-line cheat${cheat.used ? '' : ' declined'}`, this.feed);
    line.style.setProperty('--racer', racer.color);
    el('b', '', line, racer.label);
    const what = cheat.kind === 'jump' ? 'JUMP' : 'DRONE';
    el('span', '', line, ` ${cheat.used ? what : `NO ${what}`} ${Math.round(cheat.probability * 100)}%`);
    this.feed.prepend(line);
    while (this.feed.children.length > HUD.feedLength) this.feed.lastChild.remove();
  }

  /** Hide the map (play mode) so the human gets no more information than the models. */
  setMapLocked(locked) {
    this.lock.hidden = !locked;
  }

  /** Current order of racer ids (leader first). */
  order() {
    return [...this.rows.entries()]
      .sort(([, a], [, b]) => {
        if (a.finishPlace && b.finishPlace) return a.finishPlace - b.finishPlace;
        if (a.finishPlace || b.finishPlace) return a.finishPlace ? -1 : 1;
        return b.progress - a.progress;
      })
      .map(([id]) => id);
  }

  sort() {
    this.order().forEach((id, index) => {
      const entry = this.rows.get(id);
      entry.place.textContent = ordinal(index + 1).toUpperCase();
      entry.row.style.order = String(index);
      entry.row.classList.toggle('done', Boolean(entry.finishPlace));
    });
  }
}
