// Mock "models": scripted suspect lines from the case file + a regex
// note-taker. Lets you test the whole game with no AI at all, and is a
// safety net for a live demo.

export function mockSuspectReply({ suspect, history, question, evidence }) {
  const m = suspect.mock;
  if (!m) return "I've got nothing to say.";
  const q = question.toLowerCase();
  const answered = history.filter((h) => h.role === "assistant").length;
  if (answered === 0) return m.alibi;
  if (m.triggers?.some((t) => q.includes(t))) return m.cornered;
  if (evidence) return m.evidence;
  if (/where|when|what time|tonight|alibi|evening/.test(q)) return m.alibi;
  return m.deflect[answered % m.deflect.length];
}

export const HOME_WORDS = ["home", "my place", "my apartment", "my flat", "my building"];

// Does the answer actually name this place? Used to throw away claims the
// note-taker made up, so the checker never "catches" someone on words they didn't say.
export function mentionsPlace(caseData, suspect, placeId, reply) {
  const text = reply.toLowerCase();
  const place = caseData.places.get(placeId);
  const words = [place.name.toLowerCase(), ...(place.aliases ?? [])];
  if (placeId === suspect.home) words.push(...HOME_WORDS);
  if (words.some((w) => text.includes(w))) return true;
  // "the corner of Stockton and Union" names the corner the place is on.
  const streets = (place.cornerName ?? "").toLowerCase().split(" & ").map((s) => s.replace(/ (st|ave|blvd)$/, ""));
  return streets.length === 2 && streets.every((s) => text.includes(s));
}

// Was this time actually said in the answer (not just in the question)?
// If the answer gives clock times, the claim must match one of them, unless
// the answer uses relative wording ("ten minutes later") we can't check.
export function statesTime(reply, minutes) {
  const said = [...reply.matchAll(/\b(\d{1,2}):(\d{2})\b/g)].map(([, h, m]) => (Number(h) % 12) * 60 + Number(m));
  if (said.length === 0) return true;
  if (said.includes(minutes % 720)) return true;
  return /\b(later|earlier than|minutes?|hours?|half past|quarter)\b/i.test(reply);
}

const TELLS = {
  calm: ["leans back in the chair", "answers without blinking", "shrugs"],
  uneasy: ["glances at the door", "taps a finger on the table", "clears throat"],
  scared: ["wipes sweat from forehead", "voice cracks slightly", "won't meet your eyes"],
};

export function mockNotes({ caseData, suspect, question, reply, prevStress }) {
  const text = reply.toLowerCase();
  const mentions = [];
  for (const place of caseData.map.places) {
    for (const word of [place.name.toLowerCase(), ...(place.aliases ?? [])]) {
      for (let i = text.indexOf(word); i !== -1; i = text.indexOf(word, i + 1)) mentions.push({ place: place.id, idx: i });
    }
  }
  if (suspect.home) {
    for (const word of HOME_WORDS) {
      for (let i = text.indexOf(word); i !== -1; i = text.indexOf(word, i + 1)) mentions.push({ place: suspect.home, idx: i });
    }
  }
  const sentenceOf = (idx) => text.slice(0, idx).split(/[.!?]\s/).length;

  const claims = [];
  for (const match of text.matchAll(/\b(\d{1,2}):(\d{2})\b/g)) {
    let hour = Number(match[1]);
    if (hour >= 1 && hour <= 11) hour += 12;
    const time = `${String(hour).padStart(2, "0")}:${match[2]}`;
    const sentence = sentenceOf(match.index);
    const before = mentions.filter((m) => m.idx < match.index).sort((a, b) => b.idx - a.idx);
    const sameSentence = before.find((m) => sentenceOf(m.idx) === sentence);
    const after = mentions.filter((m) => m.idx > match.index && sentenceOf(m.idx) === sentence).sort((a, b) => a.idx - b.idx)[0];
    // "I got there around 8:25" refers back to the last place named.
    const pick = sameSentence ?? before[0] ?? after;
    if (!pick) continue;
    // "from 8:30 until 10:20" at the same place is one stay, not two moments.
    const prev = claims.at(-1);
    const between = prev ? text.slice(prev.end, match.index) : "";
    if (prev && prev.place === pick.place && prev.sentence === sentence && /\b(until|till|to)\b/.test(between)) {
      prev.until = time;
      prev.end = match.index + match[0].length;
      continue;
    }
    claims.push({ place: pick.place, time, until: "", label: "said so", sentence, end: match.index + match[0].length });
  }

  const q = question.toLowerCase();
  const cornered = reply === suspect.mock?.cornered;
  const pressured = suspect.mock?.triggers?.some((t) => q.includes(t));
  const stress = Math.min(100, prevStress + (cornered ? 30 : pressured ? 15 : 4));
  const band = stress > 65 ? TELLS.scared : stress > 35 ? TELLS.uneasy : TELLS.calm;
  return {
    claims: claims.map(({ place, time, until, label }) => ({ place, time, until, label })),
    stress,
    tell: band[Math.floor(Math.random() * band.length)],
  };
}
