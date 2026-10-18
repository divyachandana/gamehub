// The game loop for one question:
//   1. BIG model plays the suspect and answers.
//   2. FAST model reads the answer: which places at which times, stress, a tell.
//   3. Plain code checks every claim against real walking times + evidence.
//      No AI here, so a "lie caught" is always provably true.

import { randomUUID } from "node:crypto";
import { complete } from "./llm.js";
import { shortestPath } from "./map.js";
import { mentionsPlace, mockNotes, mockSuspectReply, statesTime } from "./mock.js";

const sessions = new Map();
const TOLERANCE_MIN = 2; // forgive small rounding in people's times
const NEAR_MIN = 3; // "near the scene" = within this many minutes' walk
const MAX_SESSIONS = 200;
const HHMM = /^([01]?\d|2[0-3]):[0-5]\d$/;

export const toMin = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};
export const fmt = (min) => {
  const h = Math.floor(min / 60) % 24;
  const m = String(Math.round(min) % 60).padStart(2, "0");
  return `${h % 12 || 12}:${m} ${h < 12 ? "AM" : "PM"}`;
};
const fmtSpan = (p) => (p.until > p.time ? `${fmt(p.time).replace(/ [AP]M$/, "")}–${fmt(p.until)}` : fmt(p.time));
const firstName = (s) => s.name.split(" ")[0];
const capitalize = (t) => t[0].toUpperCase() + t.slice(1);
const mins = (m) => `${Math.round(m)} min`;

export function createSession(caseData) {
  if (sessions.size >= MAX_SESSIONS) sessions.delete(sessions.keys().next().value);
  const session = {
    id: randomUUID(),
    caseData,
    history: Object.fromEntries(caseData.suspects.map((s) => [s.id, []])),
    stress: Object.fromEntries(caseData.suspects.map((s) => [s.id, 15])),
    // Evidence the player has. Cases with "collectEvidence": true make you find it first.
    collected: new Set(caseData.evidence.filter((e) => !caseData.collectEvidence || e.start).map((e) => e.id)),
    claims: [],
    contradictions: [],
    suspicions: [],
    questions: 0,
    over: false,
  };
  sessions.set(session.id, session);
  return session;
}

export function getSession(id) {
  const session = sessions.get(id);
  if (!session) throw Object.assign(new Error("Game not found. Start a new game."), { status: 404 });
  return session;
}

// ------------------------------------------------------------- prompts

function suspectSystem(c, s) {
  const places = c.map.places.map((p) => `${p.name} (corner of ${p.cornerName})`).join(", ");
  const role = c.labels.role.toLowerCase();
  return `You are ${s.name}, ${s.role}. You are being questioned by a ${role} about ${c.crime.summary}.

Your personality and voice: ${s.voice}

What really happened tonight. This is your private knowledge; never state it outright:
${s.truth}

Your story, which is what you tell the ${role}:
${s.cover}

If you are cornered:
${s.pressure}

Places in ${c.map.name}: ${places}. Everyone gets around this neighborhood on foot.

How to talk:
- Stay in character. Answer in 1 to 3 short spoken sentences, like a real interview. No stage directions, asterisks or narration; body language is handled separately.
- When asked where you were, give the specific times and place names from your story. All times are in the evening; say them like "9:10".
- Don't invent new exact times or places beyond your story. If pushed for details your story doesn't cover, be vague.
- Never confess and never reveal the private knowledge directly. Anything that appears only in your private knowledge (what you really did, your motive, money worries, where things are hidden) stays unsaid unless the "cornered" instructions allow it.
- When caught in an inconsistency, follow the "cornered" instructions.
- If you are shown evidence, react to that specific piece of evidence.
- Latency-sensitive; begin your visible answer immediately.`;
}

function notesSystem(c, s) {
  const places = c.map.places.map((p) => `- ${p.id}: ${p.name} (corner of ${p.cornerName})`).join("\n");
  return `You are the note-taker in an interview room. You read one answer from ${s.name} and record where ${s.name} says ${s.name} was, and when.

Known places (use only these ids):
${places}
${s.home ? `"Home" for ${s.name} means place id ${s.home}.` : `${s.name}'s home is not on the map; ignore claims about being home.`}

Rules for claims:
- One claim per place and time that ${s.name} states or clearly implies about ${s.name}'s own whereabouts. Ignore claims about other people.
- Convert times to 24-hour HH:MM. Everything happened in the evening, so "9:10" means 21:10 and "ten past nine" means 21:10.
- A stay at one place ("at Lucca from 8:30 until 10:20", "at Joe's all evening from 8 till 9:10") is ONE claim: time = start, until = end. For a single moment, until is "".
- Work out relative times ("got home five minutes later") when the base time is given.
- Leaving a place at a time counts as being there at that time. Arriving somewhere counts as being there.
- Only record a place that this answer names (or "home" when ${s.name}'s home is on the map). Streets and routes are not places: never infer a place from a route.
- Only record a time that this answer states, or that you can compute from a time it states. Skip statements with no time.
- label: a short paraphrase, at most 8 words.

Stress (0 to 100): how rattled ${s.name} is right now, starting from the previous level. What ${s.name} is sensitive about: ${s.stressHint ?? "unknown"}. Raise it sharply when the question hits a pressure point or the answer shows a story change; let it drift down for easy questions.

tell: one visible body-language cue that fits the stress level, at most 8 words, third person, no name, e.g. "glances at the door".`;
}

function notesSchema(c) {
  return {
    type: "object",
    properties: {
      claims: {
        type: "array",
        items: {
          type: "object",
          properties: {
            place: { type: "string", enum: c.map.places.map((p) => p.id) },
            time: { type: "string", description: "24-hour HH:MM" },
            until: { type: "string", description: "24-hour HH:MM end of a stay, or empty" },
            label: { type: "string" },
          },
          required: ["place", "time", "until", "label"],
          additionalProperties: false,
        },
      },
      stress: { type: "integer", description: "0 to 100" },
      tell: { type: "string" },
    },
    required: ["claims", "stress", "tell"],
    additionalProperties: false,
  };
}

// ------------------------------------------------------------- one turn

export async function ask(session, { suspectId, question, evidenceId }) {
  const c = session.caseData;
  const suspect = c.suspects.find((s) => s.id === suspectId);
  if (!suspect) throw Object.assign(new Error("Unknown suspect."), { status: 400 });
  question = String(question ?? "").trim().slice(0, 600);
  if (!question) throw Object.assign(new Error("Type a question first."), { status: 400 });
  const evidence = evidenceId && session.collected.has(evidenceId) ? c.evidence.find((e) => e.id === evidenceId) : null;
  session.questions += 1;

  const history = session.history[suspect.id];
  const content = evidence
    ? `[The ${c.labels.role.toLowerCase()} shows you evidence: "${evidence.title}": ${evidence.detail}]\n\n${question}`
    : question;

  // 1. Suspect answers.
  let reply = await complete({
    tier: "big",
    system: suspectSystem(c, suspect),
    messages: [...history, { role: "user", content }],
    maxTokens: 4000,
    mock: () => mockSuspectReply({ suspect, history, question, evidence }),
  });
  reply = reply.replace(/^["']|["']$/g, "").trim() || "...";
  history.push({ role: "user", content }, { role: "assistant", content: reply });

  // 2. Note-taker reads the answer. A failure here shouldn't kill the turn.
  const prevStress = session.stress[suspect.id];
  let notes = { claims: [], stress: prevStress, tell: "" };
  try {
    notes = await complete({
      tier: "fast",
      system: notesSystem(c, suspect),
      messages: [{ role: "user", content: `Previous stress: ${prevStress}\n\nQuestion: ${content}\n\n${suspect.name}'s answer: ${reply}` }],
      schema: notesSchema(c),
      maxTokens: 2000,
      mock: () => mockNotes({ caseData: c, suspect, question, reply, prevStress }),
    });
  } catch (err) {
    console.warn(`Note-taker failed: ${err.message}`);
  }

  const stress = Math.max(0, Math.min(100, Math.round(Number(notes.stress) || prevStress)));
  session.stress[suspect.id] = stress;

  const stays = statedStays(reply);
  const newClaims = [];
  for (const claim of Array.isArray(notes.claims) ? notes.claims : []) {
    if (!c.places.has(claim.place) || !HHMM.test(claim.time ?? "")) continue;
    // Small models sometimes invent places or copy times from the question. Keep only what was said.
    if (!mentionsPlace(c, suspect, claim.place, reply)) continue;
    const time = toMin(claim.time);
    if (!statesTime(reply, time)) continue;
    let until = HHMM.test(claim.until ?? "") ? toMin(claim.until) : time;
    if (until < time || !statesTime(reply, until)) until = time;
    // Small note-takers often drop the end of "from 10:30 to 11:15". If the answer
    // literally states that range starting at this claim's time, keep the whole stay.
    if (until === time) until = stays.find((s) => s.start === time)?.end ?? time;
    const duplicate = session.claims.some((x) => x.suspect === suspect.id && x.place === claim.place && x.time === time && x.until === until);
    if (duplicate) continue;
    const record = {
      id: `c${session.claims.length + 1}`,
      source: "claim",
      suspect: suspect.id,
      place: claim.place,
      time,
      until,
      label: String(claim.label ?? "").slice(0, 80) || "said so",
    };
    session.claims.push(record);
    newClaims.push(record);
  }

  // 3. Check the map.
  const contradictions = findContradictions(session, suspect, newClaims);
  const suspicions = findSuspicions(session, suspect);

  return {
    reply,
    tell: String(notes.tell ?? "").slice(0, 80),
    stress,
    claims: newClaims.map((x) => publicPoint(c, x)),
    contradictions,
    suspicions,
  };
}

// "from 10:30 to 11:15", "from about 8 until 9:10" -> [{start, end}] in evening minutes.
function statedStays(reply) {
  const clock = String.raw`(\d{1,2})(?::(\d{2}))?\s*(?:pm|p\.m\.)?`;
  const re = new RegExp(String.raw`\bfrom\s+(?:about\s+|around\s+)?${clock}\s+(?:to|until|till|'til)\s+(?:about\s+|around\s+)?${clock}`, "gi");
  const evening = (h, m) => (Number(h) % 12) * 60 + Number(m ?? 0) + 12 * 60;
  const stays = [];
  for (const [, h1, m1, h2, m2] of reply.matchAll(re)) {
    const start = evening(h1, m1);
    const end = evening(h2, m2);
    if (end > start) stays.push({ start, end });
  }
  return stays;
}

// Pick up a piece of evidence (cases with collectEvidence). Its facts are
// checked against everything already said.
export function collect(session, evidenceId) {
  const c = session.caseData;
  const evidence = c.evidence.find((e) => e.id === evidenceId);
  if (!evidence) throw Object.assign(new Error("Unknown evidence."), { status: 400 });
  if (session.collected.has(evidenceId)) return { evidence, contradictions: [], suspicions: [] };
  session.collected.add(evidenceId);
  const contradictions = [];
  const suspicions = [];
  for (const suspect of c.suspects) {
    const claims = session.claims.filter((x) => x.suspect === suspect.id);
    contradictions.push(...findContradictions(session, suspect, claims));
    suspicions.push(...findSuspicions(session, suspect));
  }
  return { evidence, contradictions, suspicions };
}

// ------------------------------------------------------------- the checker

function pointsFor(session, suspectId) {
  const facts = session.caseData.evidence
    .filter((e) => session.collected.has(e.id))
    .flatMap((e) =>
      (e.facts ?? [])
        .filter((f) => f.suspect === suspectId)
        .map((f, i) => {
          const time = toMin(f.time);
          return { id: `${e.id}-${i}`, source: "fact", evidence: e.title, suspect: f.suspect, place: f.place, time, until: time, label: f.label };
        }),
    );
  return [...facts, ...session.claims.filter((x) => x.suspect === suspectId)];
}

function publicPoint(c, p) {
  const place = c.places.get(p.place);
  return { ...p, placeName: place.name, cornerName: place.cornerName, timeText: fmtSpan(p) };
}

function describe(c, p, who) {
  const name = c.places.get(p.place).name;
  const when = p.until > p.time ? `from ${fmt(p.time)} until ${fmt(p.until)}` : `at ${fmt(p.time)}`;
  return p.source === "fact" ? `the ${p.evidence} puts ${who} at ${name} ${when}` : `${who} said ${name} ${when}`;
}

const dist = (c, a, b) => shortestPath(c.graph, a, b)?.minutes ?? Infinity;

function findContradictions(session, suspect, newClaims) {
  const c = session.caseData;
  const points = pointsFor(session, suspect.id);
  const who = firstName(suspect);
  const found = [];
  for (const claim of newClaims) {
    for (const other of points) {
      if (other === claim || other.place === claim.place) continue;
      const [a, b] = other.time <= claim.time ? [other, claim] : [claim, other];
      const overlap = b.time <= a.until; // both "there" at the same moment
      const gap = overlap ? 0 : b.time - a.until;
      const route = shortestPath(c.graph, a.place, b.place);
      if (!route) continue;
      if (!overlap && route.minutes <= gap + TOLERANCE_MIN) continue;
      const key = [suspect.id, `${a.place}@${a.time}-${a.until}`, `${b.place}@${b.time}-${b.until}`].join("|");
      if (session.contradictions.some((x) => x.key === key)) continue;
      const text = overlap
        ? `${capitalize(describe(c, a, who))}, but ${describe(c, b, who)}. Nobody can be in two places at once.`
        : `${capitalize(describe(c, a, who))}, and ${describe(c, b, who)}. That's ${mins(gap)} apart, but the walk takes ${mins(route.minutes)}.`;
      const item = {
        key,
        id: `x${session.contradictions.length + 1}`,
        suspect: suspect.id,
        a: publicPoint(c, a),
        b: publicPoint(c, b),
        gap,
        travel: Math.round(route.minutes * 10) / 10,
        overlap,
        path: route.path,
        roads: route.roads.filter(Boolean),
        text,
      };
      session.contradictions.push(item);
      found.push(item);
    }
  }
  return found;
}

function findSuspicions(session, suspect) {
  const c = session.caseData;
  const watch = c.watch;
  if (!watch?.window) return [];
  const [start, end] = watch.window.map(toMin);
  const watchName = c.places.get(watch.place).name;
  const who = firstName(suspect);
  const points = pointsFor(session, suspect.id).sort((a, b) => a.time - b.time);
  const found = [];
  const add = (key, item) => {
    if (session.suspicions.some((x) => x.key === key)) return;
    item = { key, id: `s${session.suspicions.length + 1}`, suspect: suspect.id, ...item };
    session.suspicions.push(item);
    found.push(item);
  };

  // Their own words put them near the scene at the right time.
  for (const p of points) {
    if (p.source !== "claim" || p.time > end + 10 || p.until < start - 10) continue;
    const away = dist(c, p.place, watch.place);
    if (away > NEAR_MIN) continue;
    const when = p.until > p.time ? `from ${fmt(p.time)} until ${fmt(p.until)}` : `at ${fmt(p.time)}`;
    // One flag per place is enough; repeating it for every statement is noise.
    add(`${suspect.id}|near|${p.place}`, {
      path: shortestPath(c.graph, p.place, watch.place).path,
      text:
        away === 0
          ? `By ${who}'s own account, ${who} was at ${watchName} ${when} (${watch.label}).`
          : `By ${who}'s own account, ${who} was at ${c.places.get(p.place).name} ${when}, a ${mins(away)} walk from ${watchName} (${watch.label}).`,
    });
  }

  // A trip between two of their points that runs right past the scene, at the right time.
  for (let i = 0; i < points.length - 1; i++) {
    const [a, b] = [points[i], points[i + 1]];
    if (a.place === b.place || a.place === watch.place || b.place === watch.place) continue;
    if (a.source === "fact" && b.source === "fact") continue; // only flag routes their own words create
    const direct = dist(c, a.place, b.place);
    const toWatch = dist(c, a.place, watch.place);
    if (toWatch + dist(c, watch.place, b.place) - direct > 1.5) continue; // not on the way
    const passAt = a.until + toWatch;
    if (passAt < start || passAt > end) continue;
    const legA = shortestPath(c.graph, a.place, watch.place);
    const legB = shortestPath(c.graph, watch.place, b.place);
    add(`${suspect.id}|route|${a.place}@${a.time}|${b.place}@${b.time}`, {
      path: [...legA.path, ...legB.path.slice(1)],
      text: `${who}'s walk from ${c.places.get(a.place).name} (${fmt(a.until)}) to ${c.places.get(b.place).name} (${fmt(b.time)}) passes right by ${watchName} at about ${fmt(passAt)} (${watch.label}).`,
    });
  }
  return found;
}

// ------------------------------------------------------------- accusation

export function accuse(session, suspectId) {
  const c = session.caseData;
  const suspect = c.suspects.find((s) => s.id === suspectId);
  if (!suspect) throw Object.assign(new Error("Unknown suspect."), { status: 400 });
  if (session.over) throw Object.assign(new Error("This game is over. Start a new one."), { status: 409 });

  const stats = { questions: session.questions, clues: session.collected.size, totalClues: c.evidence.length };
  if (!suspect.guilty) {
    session.over = true;
    return {
      outcome: "wrong",
      title: c.labels.wrong,
      text: suspect.innocentReveal ?? `${suspect.name} was telling the truth about the night.`,
      solution: c.solution.explanation,
      stats,
    };
  }
  const proof = session.contradictions.filter((x) => x.suspect === suspect.id);
  if (proof.length === 0) {
    return { outcome: "weak", title: "Not enough proof", text: c.solution.weakCase, stats };
  }
  session.over = true;
  return { outcome: "win", title: c.labels.win, text: suspect.confession, proof: proof.map((x) => x.text), solution: c.solution.explanation, stats };
}

export function route(session, from, to) {
  const c = session.caseData;
  if (!c.places.has(from) || !c.places.has(to)) throw Object.assign(new Error("Unknown place."), { status: 400 });
  const r = shortestPath(c.graph, from, to);
  return { ...r, roads: r.roads.filter(Boolean) };
}
