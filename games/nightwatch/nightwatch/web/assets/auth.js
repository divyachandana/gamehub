// Device pairing for NightWatch pages. Load this first on every page.
// - Adds the pairing token to every /api/ request.
// - If the server answers 401 (a phone on the Wi-Fi that isn't paired yet), shows a PIN prompt.
// - On the laptop's own dashboard, shows the PIN so staff can pair their phones.
// The laptop itself (localhost) never needs a token. See nightwatch/pairing.py.
(function () {
  const KEY = 'nw-token';
  let mem = null;
  const read = () => { try { return localStorage.getItem(KEY) || mem; } catch { return mem; } };
  const write = (t) => { mem = t; try { localStorage.setItem(KEY, t); } catch {} };

  const original = window.fetch.bind(window);
  let prompting = false;
  window.fetch = async (input, init = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    if (!url.startsWith('/api/') || url.startsWith('/api/pair')) return original(input, init);
    const headers = new Headers(init.headers || {});
    const token = read();
    if (token) headers.set('X-NightWatch-Token', token);
    const res = await original(input, { ...init, headers });
    if (res.status === 401 && !prompting) { prompting = true; showPrompt(); }
    return res;
  };

  function el(html) { const d = document.createElement('div'); d.innerHTML = html; return d.firstElementChild; }

  function showPrompt() {
    const box = el(`<div role="dialog" aria-modal="true" aria-labelledby="nw-pair-title" style="position:fixed;inset:0;z-index:9999;display:grid;place-items:center;background:rgba(3,6,12,.86);padding:16px;font-family:var(--nw-font,system-ui)">
      <form style="width:min(380px,100%);background:var(--nw-surface,#0e1628);border:1px solid var(--nw-line-strong,#31436a);border-radius:16px;padding:22px;color:var(--nw-ink,#ecf1fa);display:grid;gap:12px">
        <div id="nw-pair-title" style="font-weight:750;font-size:20px">Pair this phone</div>
        <div style="color:var(--nw-muted,#9ba8c0);font-size:14px">Enter the 6-digit PIN shown on the nurse-station laptop. Only paired devices can see or answer alerts.</div>
        <input name="pin" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required aria-label="PIN"
          style="font-size:30px;letter-spacing:.35em;text-align:center;padding:12px;border-radius:12px;border:1px solid var(--nw-line-strong,#31436a);background:var(--nw-bg,#070b14);color:inherit">
        <input name="name" value="Night staff phone" aria-label="Device name" maxlength="40"
          style="font-size:15px;padding:10px 12px;border-radius:10px;border:1px solid var(--nw-line,#223150);background:var(--nw-bg,#070b14);color:inherit">
        <div class="msg" role="alert" style="min-height:20px;color:var(--nw-l3,#ff5263);font-size:14px"></div>
        <button style="font:inherit;font-weight:750;font-size:17px;padding:14px;border:0;border-radius:12px;background:var(--nw-accent,#8ab4ff);color:var(--nw-accent-ink,#061430);cursor:pointer">Pair</button>
      </form></div>`);
    document.body.appendChild(box);
    const form = box.querySelector('form');
    form.pin.focus();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const r = await original('/api/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin: form.pin.value, name: form.name.value }) });
      const out = await r.json().catch(() => ({}));
      if (out.token) { write(out.token); location.reload(); }
      else { box.querySelector('.msg').textContent = out.error || 'Could not pair'; form.pin.select(); }
    };
  }

  // The nurse-station dashboard shows the PIN itself (top chips), from state.pairing.
})();
