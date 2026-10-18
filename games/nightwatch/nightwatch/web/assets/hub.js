// "◀ HUB" button (and H key) back to the game hub (gamehub repo, port 8000).
// Only on the laptop itself: staff phones on the Wi-Fi have no hub to go back to.
(function () {
  const HUB_PORT = 8000;
  if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return;
  const toHub = () => { location.href = `${location.protocol}//${location.hostname}:${HUB_PORT}/`; };

  function add() {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = '◀ HUB';
    b.title = 'Back to the game hub (H)';
    b.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:9000;padding:8px 14px;border-radius:999px;' +
      'border:1px solid var(--nw-line-strong,#31436a);background:var(--nw-surface,#0e1628);color:var(--nw-ink,#ecf1fa);' +
      'font:600 13px var(--nw-font,system-ui);cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.35)';
    b.addEventListener('click', toHub);
    document.body.append(b);
  }
  if (document.body) add(); else document.addEventListener('DOMContentLoaded', add);

  document.addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() !== 'h' || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.closest && e.target.closest('input, textarea, select, [contenteditable]')) return;
    if (document.querySelector('[role="dialog"]')) return;
    toHub();
  });
})();
