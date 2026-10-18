import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config, LLMError, warmUp } from "./src/llm.js";
import { listCases, loadCase, publicCase } from "./src/case.js";
import { accuse, ask, collect, createSession, getSession, route } from "./src/game.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.join(here, "public");
const tilesDir = path.join(here, "maps", "tiles");
const PORT = Number(process.env.PORT ?? 3000);
const TYPES = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".png": "image/png" };

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

async function readBody(req) {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 100_000) throw Object.assign(new Error("Request too large."), { status: 413 });
  }
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    throw Object.assign(new Error("Invalid JSON."), { status: 400 });
  }
}

async function serveStatic(res, pathname) {
  // Map tiles live in maps/tiles (OpenStreetMap pictures, served offline).
  const [dir, rel] = pathname.startsWith("/tiles/") ? [tilesDir, pathname.slice("/tiles".length)] : [publicDir, pathname === "/" ? "index.html" : pathname];
  const file = path.normalize(path.join(dir, rel));
  if (!file.startsWith(dir)) return send(res, 403, { error: "Forbidden" });
  try {
    const data = await readFile(file);
    res.writeHead(200, {
      "content-type": TYPES[path.extname(file)] ?? "application/octet-stream",
      ...(dir === tilesDir && { "cache-control": "public, max-age=86400" }),
    });
    res.end(data);
  } catch {
    send(res, 404, { error: "Not found" });
  }
}

const routes = {
  "GET /api/health": async () => ({ ok: true, big: config.big, fast: config.fast }),
  "GET /api/cases": async () => ({ cases: await listCases() }),
  "POST /api/new": async (body) => {
    const caseData = await loadCase(body.caseId);
    const session = createSession(caseData);
    return { sessionId: session.id, case: publicCase(caseData) };
  },
  "POST /api/ask": async (body) => ask(getSession(body.sessionId), body),
  "POST /api/accuse": async (body) => accuse(getSession(body.sessionId), body.suspectId),
  "POST /api/collect": async (body) => collect(getSession(body.sessionId), body.evidenceId),
  "GET /api/route": async (_body, url) =>
    route(getSession(url.searchParams.get("sessionId")), url.searchParams.get("from"), url.searchParams.get("to")),
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const handler = routes[`${req.method} ${url.pathname}`];
  if (!handler) {
    if (req.method === "GET" && !url.pathname.startsWith("/api/")) return serveStatic(res, url.pathname);
    return send(res, 404, { error: "Not found" });
  }
  try {
    const body = req.method === "POST" ? await readBody(req) : {};
    send(res, 200, await handler(body, url));
  } catch (err) {
    const status = err.status ?? (err instanceof LLMError ? 502 : 500);
    if (status >= 500) console.error(err);
    send(res, status, { error: err.message });
  }
});

server.listen(PORT, () => {
  const tier = (t) => `${config[t].provider} / ${config[t].model}`;
  console.log(`\n  Alibi Breaker running at http://localhost:${PORT}\n`);
  console.log(`  Suspect (big brain):    ${tier("big")}`);
  console.log(`  Note-taker (fast brain): ${tier("fast")}\n`);
  if (config.big.provider === "mock") console.log("  Mock mode: scripted answers. Set PROVIDER in .env to use real models.\n");
  warmUp();
});
