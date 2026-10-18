const $ = (sel) => document.querySelector(sel);
const esc = (t) =>
  String(t ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
const toMin = (hhmm) => hhmm.split(":").reduce((h, m) => Number(h) * 60 + Number(m));
const fmt = (min) => {
  const h = Math.floor(min / 60) % 24;
  return `${h % 12 || 12}:${String(Math.round(min) % 60).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
};
const short = (min) => fmt(min).replace(/ [AP]M$/, "");
const span = (p) => (p.until > p.time ? `${short(p.time)}–${fmt(p.until)}` : fmt(p.time));
const spanShort = (p) => (p.until > p.time ? `${short(p.time)}–${short(p.until)}` : short(p.time));
const firstName = (s) => s.name.split(" ")[0];
const initials = (s) => s.name.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

const QUICK = ["Where were you tonight?", "What time did you leave?", "Which route did you take?", "Who can confirm that?"];
const COMPOSURE = [
  [30, "Calm", "#12b76a"],
  [50, "Uneasy", "#84adff"],
  [70, "Agitated", "#f79009"],
  [101, "Distressed", "#d92d20"],
];
const composure = (v) => COMPOSURE.find(([max]) => v < max);
const PIN_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-6-5.3-6-10a6 6 0 1 1 12 0c0 4.7-6 10-6 10z"/><circle cx="12" cy="11" r="2"/></svg>`;

let state;
function resetState(data) {
  state = {
    case: data.case,
    sessionId: data.sessionId,
    active: data.case.suspects[0].id,
    chats: Object.fromEntries(data.case.suspects.map((s) => [s.id, []])),
    stress: Object.fromEntries(data.case.suspects.map((s) => [s.id, 15])),
    claims: [],
    contradictions: [],
    suspicions: [],
    evidence: null,
    highlight: null,
    pick: [],
    busy: false,
  };
}
const suspect = (id) => state.case.suspects.find((s) => s.id === id);
const place = (id) => state.case.map.places.find((p) => p.id === id);
const nodeXY = (id) => place(id) ?? state.case.map.corners.find((c) => c.id === id);
const badge = (s, cls = "") => `<span class="initials ${cls}" style="--sc:${esc(s.color)}">${esc(initials(s))}</span>`;

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error ?? `Request failed (${res.status})`);
  return data;
}

// "Gemma-4-E2B on the NPU: 0.21 s to first token · 12.6 tok/s · notes by Qwen3-1.7B: 9.8 tok/s · stress by Laya: 300 ms"
function speedText(speed) {
  if (!speed?.suspect) return "";
  const one = (s) => `${(s.firstTokenMs / 1000).toFixed(2)} s to first token${s.tokensPerSec ? ` · ${s.tokensPerSec} tok/s` : ""}`;
  const notes = speed.notes ? ` · notes by ${speed.notes.model}: ${speed.notes.tokensPerSec ?? "?"} tok/s` : "";
  const stress = speed.stress ? ` · stress by ${speed.stress.model}: ${Math.round(speed.stress.npuMs ?? 0)} ms` : "";
  return `${speed.suspect.model} on the NPU: ${one(speed.suspect)}${notes}${stress}`;
}

// ------------------------------------------------------------ case library

async function boot() {
  try {
    const h = await api("GET", "/api/health");
    const mock = h.big.provider === "mock";
    const name = (t) => t.label ?? t.model;
    const npu = h.big.provider === "npu" && h.fast.provider === "npu";
    $("#model-status").textContent = mock ? "Offline demo mode" : `${npu ? "NPU · " : ""}${name(h.big)} · ${name(h.fast)}${h.laya ? " · Laya" : ""}`;
    $("#model-status").title = `Subject model: ${h.big.provider}/${h.big.model}. Note-taker: ${h.fast.provider}/${h.fast.model}.`;
    $("#model-status").classList.toggle("mock", mock);
  } catch {
    $("#model-status").textContent = "Server unavailable";
  }
  try {
    const { cases } = await api("GET", "/api/cases");
    $("#case-list").innerHTML = cases.length
      ? cases
          .map(
            (c) => `<tr>
              <td><div class="title">${esc(c.title)}</div><div class="tagline">${esc(c.tagline)}</div></td>
              <td><span class="tag">${esc(c.category)}</span></td>
              <td>${esc(c.area.replace(", San Francisco", ""))}</td>
              <td class="num">${c.subjects ?? "—"}</td>
              <td class="num">${c.evidence ?? "—"}</td>
              <td class="num"><button class="btn primary" data-case="${esc(c.id)}">Open case</button></td>
            </tr>`,
          )
          .join("")
      : `<tr><td colspan="6" class="muted">No cases found in the cases/ folder.</td></tr>`;
  } catch (err) {
    $("#case-list").innerHTML = `<tr><td colspan="6" class="error">${esc(err.message)}</td></tr>`;
  }
}

$("#case-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-case]");
  if (!btn) return;
  btn.disabled = true;
  try {
    resetState(await api("POST", "/api/new", { caseId: btn.dataset.case }));
    showWorkspace(true);
    renderAll();
    openBriefing();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

function showWorkspace(on) {
  $("#start").hidden = on;
  $("#game").hidden = !on;
  for (const id of ["#appbar-case", "#btn-briefing", "#btn-cases", "#btn-accuse"]) $(id).hidden = !on;
}

// ------------------------------------------------------------ rendering

function renderAll() {
  const c = state.case;
  $("#case-title").textContent = c.title;
  $("#case-meta").textContent = `${c.category} · ${c.area}`;
  $("#btn-accuse").textContent = c.labels.accuse === "Accuse" ? "Make accusation" : c.labels.accuse;
  $("#subjects-title").textContent = c.labels.subjects;
  $("#legend-crime").textContent = c.crime.label;
  $("#map-title").textContent = c.map.name.replace("San Francisco · ", "San Francisco — ");
  $("#map-attribution").textContent = c.map.attribution;
  $("#quick").innerHTML = QUICK.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  renderSubjects();
  renderTranscript();
  renderMap();
  renderFindings();
  renderTimeline();
  renderEvidence();
}

function renderSubjects() {
  $("#suspects").innerHTML = state.case.suspects
    .map((s) => {
      const v = state.stress[s.id];
      const [, label, color] = composure(v);
      const n = state.contradictions.filter((x) => x.suspect === s.id).length;
      return `<button class="subject" data-suspect="${s.id}" aria-pressed="${s.id === state.active}">
        ${badge(s)}
        <span><span class="nm">${esc(s.name)}</span> ${n ? `<span class="flag">${n} inconsistenc${n > 1 ? "ies" : "y"}</span>` : ""}<br><span class="rl">${esc(s.role)}</span></span>
        <span class="composure" title="Composure: ${label}"><span class="lbl">${label}</span><span class="bar"><span style="width:${v}%;background:${color}"></span></span></span>
      </button>`;
    })
    .join("");
}

function renderTranscript() {
  const s = suspect(state.active);
  const msgs = state.chats[s.id];
  $("#interview-with").textContent = `with ${s.name}`;
  let q = 0;
  const html = msgs
    .map((m) => {
      if (m.kind === "me") {
        q += 1;
        return `<div class="entry q"><span class="det">YOU</span><div>
          <div class="who">${esc(state.case.labels.role)}<span class="q">Q${q}</span></div>
          <div class="text">${esc(m.text)}</div>${m.shown ? `<div class="presented">Presented: ${esc(m.shown)}</div>` : ""}</div></div>`;
      }
      if (m.kind === "pending") return `<div class="entry a">${badge(s, "sm")}<div><div class="who">${esc(s.name)}</div><div class="pending">Responding</div></div></div>`;
      if (m.kind === "them") {
        const rec = m.claims.map((x) => `<span class="rec">${PIN_ICON}${esc(x.placeName)} · ${esc(x.timeText)}</span>`).join("");
        return `<div class="entry a">${badge(s, "sm")}<div>
          <div class="who">${esc(s.name)}<span class="q">A${q}</span></div>
          <div class="text line">${m.animate ? "" : esc(m.text)}</div>
          ${m.tell ? `<div class="observed">Observed: <i>${esc(m.tell)}</i></div>` : ""}
          ${rec ? `<div class="recorded">${rec}</div>` : ""}
          ${m.speed ? `<div class="speed">${esc(m.speed)}</div>` : ""}</div></div>`;
      }
      return `<div class="notice ${m.kind}">${m.title ? `<b>${esc(m.title)}</b>` : ""}${esc(m.text)}</div>`;
    })
    .join("");
  const chat = $("#chat");
  chat.innerHTML = msgs.length ? html : `<div class="notice info"><b>${esc(s.name)}</b>${esc(s.bio)}</div>`;
  const pending = msgs.find((m) => m.animate);
  if (pending) {
    pending.animate = false;
    const lines = chat.querySelectorAll(".line");
    reveal(lines[lines.length - 1], pending.text);
  }
  chat.scrollTop = chat.scrollHeight;
}

// Words appear quickly rather than letter by letter: readable, not gimmicky.
function reveal(el, text) {
  if (!el) return;
  const words = text.split(/(\s+)/);
  const started = performance.now();
  const step = () => {
    const i = Math.min(words.length, Math.ceil((performance.now() - started) / 22));
    el.textContent = words.slice(0, i).join("");
    $("#chat").scrollTop = $("#chat").scrollHeight;
    if (i < words.length) setTimeout(step, 16);
  };
  step();
}

const SVG = (tag, attrs, inner = "") =>
  `<${tag} ${Object.entries(attrs)
    .map(([k, v]) => `${k}="${esc(v)}"`)
    .join(" ")}>${inner}</${tag}>`;

function renderMap() {
  const { map, crime } = state.case;
  const u = map.width / 600;
  const svg = $("#map");
  svg.setAttribute("viewBox", `0 0 ${map.width} ${map.height}`);
  let out = "";

  const { zoom, left, top } = map.tiles;
  out += `<g class="tiles">`;
  for (let tx = Math.floor(left / 256); tx <= Math.floor((left + map.width) / 256); tx++) {
    for (let ty = Math.floor(top / 256); ty <= Math.floor((top + map.height) / 256); ty++) {
      out += SVG("image", { href: `/tiles/${zoom}/${tx}/${ty}.png`, x: tx * 256 - left, y: ty * 256 - top, width: 256.6, height: 256.6, preserveAspectRatio: "none" });
    }
  }
  out += `</g>`;

  if (state.highlight?.path?.length > 1) {
    const pts = state.highlight.path.map(nodeXY).filter(Boolean).map((n) => `${n.x},${n.y}`).join(" ");
    out += SVG("polyline", { points: pts, fill: "none", stroke: "#fff", "stroke-width": 9 * u, "stroke-linecap": "round", "stroke-linejoin": "round" });
    out += SVG("polyline", { class: `route ${state.highlight.kind}`, points: pts, "stroke-width": 4.5 * u, ...(state.highlight.kind === "check" && { "stroke-dasharray": `${10 * u} ${7 * u}` }) });
  }

  const pins = new Map();
  const add = (pt) => pins.set(pt.place, [...(pins.get(pt.place) ?? []), pt]);
  for (const e of state.case.evidence) {
    for (const f of e.facts ?? []) {
      const time = toMin(f.time);
      add({ ...f, time, until: time, source: "fact", evidence: e.title });
    }
  }
  for (const c of state.claims) add(c);
  let pinsOut = "";
  for (const [pid, list] of pins) {
    const p = place(pid);
    const toLeft = p.x > map.width - 140 * u;
    list.sort((a, b) => a.time - b.time);
    list.forEach((pt, i) => {
      const s = pt.suspect ? suspect(pt.suspect) : null;
      const label = spanShort(pt);
      const w = (label.length * 6 + 12) * u;
      const h = 16 * u;
      const x = toLeft ? p.x - 14 * u - w : p.x + 14 * u;
      const y = p.y - h / 2 - (list.length - 1 - i) * (h + 3 * u);
      const dim = s && s.id !== state.active ? 0.3 : 1;
      const fact = pt.source === "fact";
      const color = s?.color ?? "#667085";
      const what = fact ? `${pt.evidence}: ${pt.label}` : `Statement: ${pt.label}`;
      pinsOut += `<g class="pin" opacity="${dim}">
        ${SVG("rect", { x, y, width: w, height: h, rx: 3 * u, fill: fact ? "#fff" : color, stroke: fact ? color : "none", "stroke-width": 1.4 * u }, `<title>${esc(s?.name ?? "Unidentified")} · ${esc(label)} · ${esc(what)}</title>`)}
        ${SVG("text", { x: x + w / 2, y: y + h / 2 + 3.8 * u, "text-anchor": "middle", "font-size": 10.5 * u, fill: fact ? "#344054" : "#fff" }, esc(label))}
      </g>`;
    });
  }

  for (const p of map.places) {
    const scene = p.id === crime.place;
    const r = 6.5 * u;
    const lw = (p.name.length * 6.1 + 14) * u;
    const ly = p.y + r + 5 * u;
    out += `<g class="place${scene ? " scene" : ""}${state.pick.includes(p.id) ? " picked" : ""}" data-place="${p.id}">
      ${SVG("circle", { cx: p.x, cy: p.y, r: 18 * u, fill: "transparent" })}
      ${SVG("circle", { class: "dot", cx: p.x, cy: p.y, r, "stroke-width": (scene ? 3 : 2.2) * u }, `<title>${esc(p.name)} · ${esc(p.cornerName)}</title>`)}
      ${SVG("rect", { class: "label-bg", x: p.x - lw / 2, y: ly, width: lw, height: 17 * u, rx: 3 * u, "stroke-width": 0.8 * u })}
      ${SVG("text", { x: p.x, y: ly + 12 * u, "text-anchor": "middle", "font-size": 10.5 * u }, esc(p.name))}
    </g>`;
  }
  svg.innerHTML = out + pinsOut;
}

function renderFindings() {
  const items = [
    ...state.contradictions.map((x) => ({ ...x, kind: "contradiction" })),
    ...state.suspicions.map((x) => ({ ...x, kind: "suspicion" })),
  ].reverse();
  $("#findings-count").textContent = items.length ? `${items.length}` : "";
  $("#alerts").innerHTML = items.length
    ? items
        .map(
          (x) => `<button class="finding ${x.kind}${state.highlight?.id === x.id ? " active" : ""}" data-alert="${x.id}">
            <span class="kind">${x.kind === "contradiction" ? "Inconsistency" : "Route of interest"} · ${esc(suspect(x.suspect).name)}</span>${esc(x.text)}</button>`,
        )
        .join("")
    : `<p class="empty">No findings yet. Statements are checked automatically as they are recorded.</p>`;
}

function renderTimeline() {
  const s = suspect(state.active);
  $("#timeline-who").textContent = s.name;
  const points = [
    ...state.case.evidence.flatMap((e) =>
      (e.facts ?? [])
        .filter((f) => f.suspect === s.id)
        .map((f) => ({ ...f, time: toMin(f.time), until: toMin(f.time), source: "fact", evidence: e.title })),
    ),
    ...state.claims.filter((c) => c.suspect === s.id),
  ].sort((a, b) => a.time - b.time);
  $("#timeline").innerHTML = points.length
    ? points
        .map(
          (p) => `<li><span class="t">${esc(span(p))}</span><span><span class="kindtag">${p.source === "fact" ? "Evidence" : "Stated"}</span>${esc(place(p.place).name)}
            <span class="src">${p.source === "fact" ? esc(p.evidence) : p.label && p.label !== "said so" ? `“${esc(p.label)}”` : "Interview statement"}</span></span></li>`,
        )
        .join("")
    : `<li><span class="empty">No entries for ${esc(firstName(s))} yet.</span></li>`;
}

function renderEvidence() {
  const s = suspect(state.active);
  $("#evidence-count").textContent = `${state.case.evidence.length} items`;
  $("#evidence").innerHTML = state.case.evidence
    .map(
      (e) => `<div class="ev"><h3>${esc(e.title)}</h3><p>${esc(e.detail)}</p>
        <button type="button" class="btn" data-evidence="${e.id}">Present to ${esc(firstName(s))}</button></div>`,
    )
    .join("");
  const ev = state.case.evidence.find((e) => e.id === state.evidence);
  $("#evidence-chip").hidden = !ev;
  if (ev) $("#evidence-chip-title").textContent = ev.title;
}

// ------------------------------------------------------------ interactions

$("#suspects").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-suspect]");
  if (!btn) return;
  state.active = btn.dataset.suspect;
  renderSubjects();
  renderTranscript();
  renderMap();
  renderTimeline();
  renderEvidence();
  $("#question").focus();
});

$("#quick").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-q]");
  if (!btn) return;
  $("#question").value = btn.dataset.q;
  $("#ask-form").requestSubmit();
});

$("#question").addEventListener("input", () => ($("#ask-error").hidden = true));

$("#evidence").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-evidence]");
  if (!btn) return;
  state.evidence = btn.dataset.evidence;
  renderEvidence();
  $("#question").focus();
  if (!$("#question").value) $("#question").value = "Can you explain this?";
});
$("#evidence-chip-clear").addEventListener("click", () => {
  state.evidence = null;
  renderEvidence();
});

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.busy) return;
  const question = $("#question").value.trim();
  if (!question) {
    $("#ask-error").textContent = "Enter a question first.";
    $("#ask-error").hidden = false;
    return;
  }
  const suspectId = state.active;
  const chat = state.chats[suspectId];
  const evidence = state.case.evidence.find((x) => x.id === state.evidence);
  state.busy = true;
  $("#btn-ask").disabled = true;
  $("#question").value = "";
  chat.push({ kind: "me", text: question, shown: evidence?.title });
  const pending = { kind: "pending" };
  chat.push(pending);
  renderTranscript();

  try {
    const r = await api("POST", "/api/ask", { sessionId: state.sessionId, suspectId, question, evidenceId: evidence?.id });
    chat.splice(chat.indexOf(pending), 1);
    chat.push({ kind: "them", text: r.reply, tell: r.tell, claims: r.claims, speed: speedText(r.speed), animate: suspectId === state.active });
    state.stress[suspectId] = r.stress;
    state.claims.push(...r.claims);
    state.contradictions.push(...r.contradictions);
    state.suspicions.push(...r.suspicions);
    for (const x of r.contradictions) chat.push({ kind: "alert", title: "Inconsistency detected", text: x.text });
    for (const x of r.suspicions) chat.push({ kind: "warn", title: "Route of interest", text: x.text });
    const latest = r.contradictions.at(-1) ?? r.suspicions.at(-1);
    if (latest) state.highlight = { id: latest.id, kind: r.contradictions.length ? "contradiction" : "suspicion", path: latest.path };
    state.evidence = null;
  } catch (err) {
    chat.splice(chat.indexOf(pending), 1);
    chat.push({ kind: "error", title: "Request failed", text: err.message });
  } finally {
    state.busy = false;
    $("#btn-ask").disabled = false;
    if (suspectId === state.active) $("#question").focus();
    renderAll();
  }
});

$("#alerts").addEventListener("click", (e) => {
  const card = e.target.closest("[data-alert]");
  if (!card) return;
  const x = [...state.contradictions, ...state.suspicions].find((y) => y.id === card.dataset.alert);
  state.highlight = { id: x.id, kind: x.id.startsWith("x") ? "contradiction" : "suspicion", path: x.path };
  state.active = x.suspect;
  renderAll();
});

$("#map").addEventListener("click", async (e) => {
  const g = e.target.closest("[data-place]");
  if (!g) return;
  const id = g.dataset.place;
  state.pick = state.pick[0] === id ? [] : [...state.pick, id].slice(-2);
  if (state.pick.length === 2) {
    const [from, to] = state.pick;
    try {
      const r = await api("GET", `/api/route?${new URLSearchParams({ sessionId: state.sessionId, from, to })}`);
      state.highlight = { id: null, kind: "check", path: r.path };
      $("#route-readout").innerHTML = `${esc(place(from).name)} → ${esc(place(to).name)}: <b>${Math.round(r.minutes)} min</b> walk`;
    } catch (err) {
      $("#route-readout").textContent = err.message;
    }
    state.pick = [];
  } else if (state.pick.length === 1) {
    $("#route-readout").textContent = `From ${place(id).name}. Select a second location.`;
  }
  renderMap();
});

// ------------------------------------------------------------ dialogs

function openBriefing() {
  const c = state.case;
  $("#briefing-kicker").textContent = `${c.category} · ${c.area}`;
  $("#briefing-title").textContent = c.title;
  $("#briefing-text").textContent = c.briefing;
  $("#briefing-people").innerHTML = c.suspects
    .map((s) => `<div class="person">${badge(s)}<div><b>${esc(s.name)}</b><span>${esc(s.role)}</span></div></div>`)
    .join("");
  $("#briefing-dialog").showModal();
}
$("#btn-briefing").addEventListener("click", openBriefing);

// Back to the game hub (gamehub repo), like the racing games' HUB button.
const HUB_PORT = 8000;
const toHub = () => (location.href = `${location.protocol}//${location.hostname}:${HUB_PORT}/`);
$("#btn-hub").addEventListener("click", toHub);
document.addEventListener("keydown", (e) => {
  if (e.key.toLowerCase() !== "h" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest("input, textarea, select, [contenteditable]") || document.querySelector("dialog[open]")) return;
  toHub();
});
$("#btn-cases").addEventListener("click", () => showWorkspace(false));

$("#btn-accuse").addEventListener("click", () => {
  $("#accuse-heading").textContent = $("#btn-accuse").textContent;
  $("#accuse-options").innerHTML = state.case.suspects
    .map((s) => {
      const n = state.contradictions.filter((x) => x.suspect === s.id).length;
      return `<button data-accuse="${s.id}">${badge(s)}<span><b>${esc(s.name)}</b>
        <small>${n ? `${n} inconsistenc${n > 1 ? "ies" : "y"} on record` : "No inconsistencies on record"}</small></span></button>`;
    })
    .join("");
  $("#accuse-pick").hidden = false;
  $("#accuse-result").hidden = true;
  $("#accuse-dialog").showModal();
});

$("#accuse-options").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-accuse]");
  if (!btn) return;
  try {
    const r = await api("POST", "/api/accuse", { sessionId: state.sessionId, suspectId: btn.dataset.accuse });
    $("#result-kicker").textContent = { win: "Outcome", weak: "Insufficient evidence", wrong: "Outcome" }[r.outcome];
    $("#result-title").textContent = r.title;
    $("#result-title").className = r.outcome;
    $("#result-text").textContent = r.text;
    $("#result-proof-title").hidden = !r.proof?.length;
    $("#result-proof").innerHTML = (r.proof ?? []).map((p) => `<li>${esc(p)}</li>`).join("");
    $("#result-solution").textContent = r.solution ?? "";
    $("#result-solution").previousElementSibling.hidden = !r.solution;
    $("#result-stats").textContent = r.outcome === "weak" ? "" : `${r.stats.questions} question${r.stats.questions === 1 ? "" : "s"} asked.`;
    $("#btn-result-new").hidden = r.outcome === "weak";
    $("#accuse-pick").hidden = true;
    $("#accuse-result").hidden = false;
  } catch (err) {
    alert(err.message);
  }
});
$("#btn-result-close").addEventListener("click", () => $("#accuse-dialog").close());
$("#btn-result-new").addEventListener("click", () => {
  $("#accuse-dialog").close();
  showWorkspace(false);
});

boot();
