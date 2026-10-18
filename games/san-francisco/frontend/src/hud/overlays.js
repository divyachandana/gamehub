/**
 * Full-screen moments: the title / attract screen, the 3-2-1-GO countdown and the
 * results podium.
 */

import { ordinal } from './racer-hud.js';

export class Overlays {
  /** @param {HTMLElement} root Overlay container covering the panes. */
  constructor(root) {
    this.root = root;
    this.countdownTimer = null;
    this.place = null; // the street map's title, once a race with one is announced
    this.logo = 'SAN<br><span>FRANCISCO</span>'; // the title-screen logo
  }

  /** Title screen shown before the first race. */
  showTitle() {
    this.root.className = 'overlay title';
    this.root.innerHTML = `
      <div class="title-logo">${this.logo}</div>
      <div class="title-sub">ON-DEVICE AI MODELS RACE THROUGH ${(this.place ?? 'a maze').toUpperCase()}</div>
      <div class="title-sub dim">EVERY TURN IS A DECISION · EVERY SECOND IS COMPUTE</div>
      <div class="blink">PRESS ENTER TO START</div>`;
  }

  /** Waiting for the server (no race yet / disconnected). @param {string} text */
  showMessage(text) {
    this.root.className = 'overlay message';
    this.root.innerHTML = `<div class="blink">${text}</div>`;
  }

  /** @param {number|string} value 3, 2, 1 or "GO!" */
  showCountdown(value) {
    this.root.className = 'overlay countdown';
    this.root.innerHTML = `<div class="count">${value}</div>`;
    clearTimeout(this.countdownTimer);
    if (value === 'GO!') this.countdownTimer = setTimeout(() => this.hide(), 900);
  }

  hide() {
    this.root.className = 'overlay hidden';
    this.root.innerHTML = '';
  }

  /**
   * Final results.
   * @param {object[]} racers Racer views.
   * @param {Record<string, object>} results RacerStats keyed by racer id.
   * @param {number} optimalSegments Length of the A* route.
   */
  showPodium(racers, results, optimalSegments) {
    const ranked = racers
      .map((racer) => ({ racer, stats: results[racer.id] }))
      .sort((a, b) => (a.stats.place ?? 99) - (b.stats.place ?? 99));
    const winner = ranked[0];
    const rogues = racers.some((racer) => racer.rogue);
    const rows = ranked
      .map(({ racer, stats }) => {
        const human = racer.runtime === 'human';
        const efficiency = stats.segments_driven ? Math.round((optimalSegments / stats.segments_driven) * 100) : 0;
        const time = stats.finish_time_s == null ? 'DNF' : `${stats.finish_time_s.toFixed(1)}S`;
        return `<tr style="--racer:${racer.color}">
          <td>${stats.place ? ordinal(stats.place).toUpperCase() : '-'}</td>
          <td class="who">${racer.label}<small>${racer.name}</small></td>
          <td>${time}</td>
          <td>${human ? '-' : stats.avg_tps.toFixed(1)}</td>
          <td>${Math.round(stats.avg_ttft_ms)}</td>
          <td>${human ? '-' : stats.tokens_total}</td>
          <td>${stats.wrong_turns}</td>
          <td>${efficiency}%</td>
          ${rogues ? `<td>${racer.rogue ? stats.cheats : '-'}</td>` : ''}
        </tr>`;
      })
      .join('');
    this.root.className = 'overlay podium';
    this.root.innerHTML = `
      <div class="podium-winner" style="--racer:${winner.racer.color}">${winner.racer.label} WINS!</div>
      <table class="results">
        <thead><tr><th>POS</th><th>RACER</th><th>TIME</th><th>TOK/S</th><th>THINK MS</th><th>TOKENS</th><th>WRONG</th><th>ROUTE</th>${rogues ? '<th>CHEATS</th>' : ''}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="blink">ENTER = REMATCH · N = NEW MAZE</div>`;
  }
}
