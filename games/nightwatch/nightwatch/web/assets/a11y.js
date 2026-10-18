// NightWatch display modes: High contrast, Larger text, Reduce motion.
// Load in <head> (right after auth.js) so the classes land on <html> before first paint.
// Saved per device in localStorage ("nw-display"); other open NightWatch tabs follow along.
// Mounts a "Display" menu into every element with [data-nw-display], and the shared screen list into
// every <nav data-nw-nav>. Styles: /assets/a11y.css.
(function () {
  const KEY = 'nw-display';
  const root = document.documentElement;
  const mq = (q) => (window.matchMedia ? window.matchMedia(q) : { matches: false, addEventListener() {} });
  const sysMotion = mq('(prefers-reduced-motion: reduce)');
  const sysContrast = mq('(prefers-contrast: more)');
  const OPTS = [
    { key: 'hc', cls: 'nw-hc', label: 'High contrast', hint: 'Black background, brighter text, thicker borders' },
    { key: 'lg', cls: 'nw-lg', label: 'Larger text', hint: 'Everything 20 % bigger. Good for the projector' },
    { key: 'rm', cls: 'nw-rm', label: 'Reduce motion', hint: 'No pulsing, sliding or animated rings' },
  ];

  const read = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
  const write = (s) => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private mode: this page only */ } };
  // Unset = follow the system: high contrast if the OS asks for more contrast.
  const effective = (s) => ({ hc: s.hc ?? sysContrast.matches, lg: !!s.lg, rm: !!s.rm || sysMotion.matches });

  let state = read();
  function apply() {
    const e = effective(state);
    OPTS.forEach((o) => root.classList.toggle(o.cls, !!e[o.key]));
    document.querySelectorAll('.nwd-opt input').forEach((i) => {
      i.checked = !!e[i.dataset.opt];
      const locked = i.dataset.opt === 'rm' && sysMotion.matches;
      i.disabled = locked;
      i.closest('.nwd-opt').classList.toggle('is-locked', locked);
      const sm = i.closest('.nwd-opt').querySelector('small');
      if (i.dataset.opt === 'rm') sm.textContent = locked ? 'On because your device asks for reduced motion' : OPTS[2].hint;
    });
  }
  function set(key, on) { state = { ...state, [key]: on }; write(state); apply(); }
  apply();   // runs in <head>: no flash of the wrong mode

  window.addEventListener('storage', (e) => { if (e.key === KEY) { state = read(); apply(); } });
  sysMotion.addEventListener?.('change', apply);
  sysContrast.addEventListener?.('change', apply);

  const ICON = '<svg class="nw-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>';
  let n = 0;
  function mount(host) {
    if (host.dataset.nwDisplayMounted) return;
    host.dataset.nwDisplayMounted = '1';
    const id = `nwd-panel-${++n}`;
    host.innerHTML = `<div class="nwd">
      <button type="button" class="nw-btn nw-btn--ghost nwd-btn" aria-expanded="false" aria-controls="${id}">${ICON}Display</button>
      <div class="nwd-panel" id="${id}" role="group" aria-label="Display options" hidden>
        <div class="nwd-head">Display · this device</div>
        ${OPTS.map((o) => `<label class="nwd-opt"><input type="checkbox" data-opt="${o.key}"><b>${o.label}</b><small>${o.hint}</small></label>`).join('')}
        <div class="nwd-foot"><small>Saved on this device</small><button type="button" class="nwd-reset">Reset</button></div>
      </div></div>`;
    const btn = host.querySelector('.nwd-btn'), panel = host.querySelector('.nwd-panel');
    const place = () => {
      const r = btn.getBoundingClientRect(), w = Math.min(330, innerWidth - 32);
      panel.style.top = `${Math.round(r.bottom + 8)}px`;
      panel.style.left = `${Math.round(Math.max(16, Math.min(r.right - w, innerWidth - 16 - w)))}px`;
    };
    const open = (on) => {
      btn.setAttribute('aria-expanded', String(on)); panel.hidden = !on;
      if (on) { place(); panel.querySelector('input:not(:disabled)')?.focus(); }
    };
    btn.addEventListener('click', () => open(panel.hidden));
    panel.addEventListener('change', (e) => { const i = e.target.closest('input[data-opt]'); if (i) set(i.dataset.opt, i.checked); });
    panel.querySelector('.nwd-reset').addEventListener('click', () => { state = {}; write(state); apply(); });
    host.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !panel.hidden) { e.stopPropagation(); open(false); btn.focus(); } });
    document.addEventListener('pointerdown', (e) => { if (!panel.hidden && !host.contains(e.target)) open(false); });
    host.addEventListener('focusout', (e) => { if (!panel.hidden && e.relatedTarget && !host.contains(e.relatedTarget)) open(false); });
    window.addEventListener('resize', () => { if (!panel.hidden) place(); });
    window.addEventListener('scroll', () => { if (!panel.hidden) place(); }, { passive: true });
    apply();
  }
  // Consistent navigation (WCAG 3.2.3): the same screens, in the same order, on every laptop page.
  // <nav data-nw-nav></nav> is filled in; the current page gets aria-current="page".
  const SCREENS = [['/home/', 'Home'], ['/dashboard/', 'Nurse station'], ['/staff/', 'Staff phone'], ['/camera/', 'Camera'],
    ['/sim/', 'Presenter'], ['/report/', 'Shift report'], ['/perf/', 'Performance'], ['/help/', 'Help'], ['/settings/', 'Settings']];
  function mountNav(nav) {
    if (nav.dataset.nwNavMounted) return;
    nav.dataset.nwNavMounted = '1';
    nav.classList.add('nw-nav');
    if (!nav.hasAttribute('aria-label')) nav.setAttribute('aria-label', 'NightWatch screens');
    const here = location.pathname.replace(/index\.html$/, '');
    nav.innerHTML = SCREENS.map(([href, label]) => `<a href="${href}"${here === href ? ' aria-current="page"' : ''}>${label}</a>`).join('');
  }
  const mountAll = () => { document.querySelectorAll('[data-nw-display]').forEach(mount); document.querySelectorAll('[data-nw-nav]').forEach(mountNav); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountAll); else mountAll();

  // For page scripts: smooth scrolling only when motion is welcome.
  window.NWDisplay = { get: () => effective(state), set, mount, mountNav, screens: SCREENS, scrollBehavior: () => (effective(state).rm ? 'auto' : 'smooth') };
})();
