// Gemma-4-E2B and Qwen3-1.7B on the Snapdragon NPU.
//
// The models run in ExecuTorch's qnn_llm_host.exe (QNN backend), the same host
// processes the racing games use: one per model, each serving newline-delimited
// JSON on a Windows named pipe (\\.\pipe\rally_gemma, \\.\pipe\rally_qwen).
// If a host is already running (another game started it), we just connect;
// otherwise we start it and leave it running for the next game.
//
// Every request is one-shot ("reset": true), so the chat history is folded into
// the prompt and trimmed to fit the model's compiled context.

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, openSync, readFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const NPU_DIR = path.resolve(process.env.NPU_DIR ?? path.join(here, "..", "..", "..", "npu"));
const QNN_SDK = process.env.QNN_SDK_ROOT ?? "C:/Qualcomm/AIStack/QAIRT/2.46.0.260424";
const HEXAGON = process.env.HEXAGON_ARCH ?? "v73"; // Snapdragon X Elite (SC8380XP)
const LOG_DIR = path.join(here, "..", "logs");
const START_TIMEOUT_MS = 120_000;
const CHARS_PER_TOKEN = 3; // under-counts characters per token, so token estimates run high (safe)
const TEMPLATE_TOKENS = 48; // chat-template tokens around system + prompt

// Same models, pipes and limits as the racing games (racers.yaml, qnn:).
export const NPU_MODELS = {
  "gemma-4-e2b": {
    label: "Gemma-4-E2B",
    dir: "models/gemma4_e2b_sc8380xp_ctxt_4000_ar_l-128",
    pte: "gemma4_e2b_sc8380xp_ctxt_4000-001.pte",
    version: "gemma4",
    pipe: "rally_gemma",
    maxSeqLen: 3800, // compiled context is 4000
    suffix: "",
  },
  "qwen3-1.7b": {
    label: "Qwen3-1.7B",
    dir: "models/sc8380_qwen3-1_7b_ctxt-8000",
    pte: "sc8380_qwen3-1_7b_ctxt-8000.pte",
    version: "qwen3",
    pipe: "rally_qwen",
    maxSeqLen: 7800, // compiled context is 8000
    suffix: " /no_think", // otherwise Qwen3 reasons for hundreds of tokens first
  },
};

export class NpuError extends Error {}

const estimate = (text) => Math.ceil(text.length / CHARS_PER_TOKEN);

// --------------------------------------------------------------- host process

class Host {
  constructor(id) {
    this.id = id;
    this.model = NPU_MODELS[id];
    this.socket = null;
    this.queue = Promise.resolve();
    this.onLine = null;
  }

  // One request at a time per host: the host serves one generation at a time.
  request(payload, onDelta) {
    const run = this.queue.then(() => this.#send(payload, onDelta));
    this.queue = run.catch(() => {});
    return run;
  }

  async #send(payload, onDelta) {
    const socket = await this.#connect();
    return new Promise((resolve, reject) => {
      this.onLine = (msg) => {
        if (!("ok" in msg)) return msg.delta && onDelta?.(msg.delta);
        this.onLine = null;
        if (msg.ok) resolve(msg);
        else reject(new NpuError(`${this.model.label} on the NPU failed: ${msg.error ?? "unknown error"}`));
      };
      this.onFail = (err) => {
        this.onLine = null;
        reject(new NpuError(`${this.model.label} NPU host stopped answering (${err}). See logs/qnn_${this.id}.log.`));
      };
      socket.write(`${JSON.stringify(payload)}\n`);
    });
  }

  async #connect() {
    if (this.socket) return this.socket;
    const pipe = `\\\\.\\pipe\\${this.model.pipe}`;
    const started = Date.now();
    let launched = null;
    for (;;) {
      try {
        this.socket = await openPipe(pipe);
        break;
      } catch (err) {
        if (!launched && err.code === "ENOENT") {
          launched = this.#launch();
          console.log(`  [npu] starting ${this.model.label} on the NPU (pipe ${this.model.pipe})...`);
        }
        if (launched?.exitCode != null) {
          throw new NpuError(`${this.model.label} NPU host exited (code ${launched.exitCode}). ${logTail(this.id)}`);
        }
        if (Date.now() - started > START_TIMEOUT_MS) {
          throw new NpuError(`${this.model.label} NPU host didn't open \\\\.\\pipe\\${this.model.pipe} in time. ${logTail(this.id)}`);
        }
        await new Promise((r) => setTimeout(r, 300));
      }
    }
    if (launched) console.log(`  [npu] ${this.model.label} loaded in ${((Date.now() - started) / 1000).toFixed(1)}s`);
    else console.log(`  [npu] using the running ${this.model.label} host (pipe ${this.model.pipe})`);

    let buffer = "";
    this.socket.setEncoding("utf8");
    this.socket.on("data", (chunk) => {
      buffer += chunk;
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        try {
          this.onLine?.(JSON.parse(line));
        } catch {}
      }
    });
    const drop = (why) => {
      this.socket = null;
      this.onFail?.(why);
    };
    this.socket.on("error", (err) => drop(err.code ?? err.message));
    this.socket.on("close", () => drop("pipe closed"));
    return this.socket;
  }

  // Started detached: the host outlives this game, so the next game reuses the
  // loaded model. The game hub shuts the hosts down when it exits.
  #launch() {
    const m = this.model;
    const modelDir = path.join(NPU_DIR, m.dir);
    const exe = path.join(NPU_DIR, "host", "qnn_llm_host.exe");
    for (const file of [exe, path.join(modelDir, m.pte), path.join(modelDir, "tokenizer.json")]) {
      if (!existsSync(file)) throw new NpuError(`Missing ${file}. See docs/models.md in the gamehub repo.`);
    }
    mkdirSync(LOG_DIR, { recursive: true });
    const log = openSync(path.join(LOG_DIR, `qnn_${this.id}.log`), "w");
    const env = {
      ...process.env,
      QNN_SDK_ROOT: QNN_SDK,
      PATH: [path.join(NPU_DIR, "host"), path.join(QNN_SDK, "lib", "aarch64-windows-msvc"), process.env.PATH].join(path.delimiter),
      ADSP_LIBRARY_PATH: path.join(QNN_SDK, "lib", `hexagon-${HEXAGON}`, "unsigned"),
    };
    const args = [
      "--model_path", path.join(modelDir, m.pte),
      "--tokenizer_path", path.join(modelDir, "tokenizer.json"),
      "--decoder_model_version", m.version,
      "--pipe_name", m.pipe,
      "--seq_len", String(m.maxSeqLen),
      "--performance_output_path", path.join(LOG_DIR, `qnn_${this.id}_speed.txt`),
    ];
    const proc = spawn(exe, args, { env, cwd: LOG_DIR, detached: true, windowsHide: true, stdio: ["ignore", log, log] });
    proc.on("error", () => {});
    proc.unref();
    return proc;
  }
}

function openPipe(pipe) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(pipe);
    socket.once("connect", () => {
      socket.removeListener("error", reject);
      resolve(socket);
    });
    socket.once("error", reject);
  });
}

function logTail(id) {
  try {
    const lines = readFileSync(path.join(LOG_DIR, `qnn_${id}.log`), "utf8").trim().split(/\r?\n/);
    return `Last log line: ${lines.at(-1)}`;
  } catch {
    return `See logs/qnn_${id}.log.`;
  }
}

const hosts = {};

// --------------------------------------------------------------- prompts

// The host takes one system prompt and one user turn, so earlier turns of the
// interview become a transcript inside the prompt. Oldest turns go first when
// the context is tight.
function foldHistory(messages, budgetChars) {
  const last = messages.at(-1).content;
  const earlier = messages.slice(0, -1);
  const turns = [];
  let used = last.length;
  for (let i = earlier.length - 1; i >= 0; i--) {
    const line = `${earlier[i].role === "user" ? "Q" : "You"}: ${earlier[i].content}`;
    if (used + line.length > budgetChars) break;
    turns.unshift(line);
    used += line.length + 1;
  }
  if (!turns.length) return last;
  return `The interview so far (Q = the question you were asked):\n${turns.join("\n")}\n\nThe next question:\n${last}`;
}

function schemaHint(schema) {
  return `\n\nReply with only one JSON object on a single line, no other text, matching this JSON schema:\n${JSON.stringify(schema)}`;
}

// Small models sometimes break the JSON (a stray "]", a missing comma). Rebuild
// the object from the pieces the schema asks for: flat objects for arrays of
// objects, and "key": value pairs for numbers and strings.
export function salvageJson(text, schema) {
  try {
    return JSON.stringify(JSON.parse(text.match(/\{[\s\S]*\}/)?.[0] ?? text));
  } catch {}
  const out = {};
  for (const [key, prop] of Object.entries(schema.properties ?? {})) {
    if (prop.type === "array" && prop.items?.type === "object") {
      out[key] = [...text.matchAll(/\{[^{}]*\}/g)]
        .map(([obj]) => {
          try {
            return JSON.parse(obj);
          } catch {
            return null;
          }
        })
        .filter((obj) => obj && (prop.items.required ?? []).every((k) => k in obj));
    } else if (prop.type === "integer" || prop.type === "number") {
      const num = text.match(new RegExp(`"${key}"\\s*:\\s*(-?\\d+(?:\\.\\d+)?)`));
      if (num) out[key] = Number(num[1]);
    } else if (prop.type === "string") {
      const str = text.match(new RegExp(`"${key}"\\s*:\\s*("(?:[^"\\\\]|\\\\.)*")`));
      if (str) out[key] = JSON.parse(str[1]);
    }
  }
  return JSON.stringify(out);
}

// --------------------------------------------------------------- one call

// Returns { text, stats } where stats are measured here, the same way for both models.
export async function npuComplete({ model, system, messages, schema, replyTokens }) {
  const m = NPU_MODELS[model];
  if (!m) throw new NpuError(`Unknown NPU model "${model}". Use ${Object.keys(NPU_MODELS).join(" or ")}.`);
  hosts[model] ??= new Host(model);

  const systemPrompt = system + (schema ? schemaHint(schema) : "");
  const budgetTokens = m.maxSeqLen - replyTokens - TEMPLATE_TOKENS - estimate(systemPrompt + m.suffix);
  if (budgetTokens < 64) throw new NpuError(`The ${m.label} system prompt is too long for its context.`);
  const prompt = foldHistory(messages, budgetTokens * CHARS_PER_TOKEN) + m.suffix;
  const seqLen = Math.min(m.maxSeqLen, estimate(systemPrompt + prompt) + TEMPLATE_TOKENS + replyTokens);

  const started = performance.now();
  let firstAt = null;
  const reply = await hosts[model].request(
    {
      prompt,
      system_prompt: systemPrompt,
      seq_len: seqLen,
      temperature: schema ? 0.0 : 0.6,
      reset: true,
      stream: true,
      include_metrics: true,
    },
    () => (firstAt ??= performance.now()),
  );
  const done = performance.now();
  const tokens = reply.metrics?.generated_tokens ?? null;
  const stats = {
    model: m.label,
    promptTokens: reply.metrics?.prompt_tokens ?? null,
    tokens,
    firstTokenMs: Math.round((firstAt ?? done) - started),
    tokensPerSec: tokens && firstAt && done > firstAt ? Math.round(((tokens - 1) / ((done - firstAt) / 1000)) * 10) / 10 : null,
    totalMs: Math.round(done - started),
  };
  let text = String(reply.text ?? "")
    .replace(/<think>[\s\S]*?<\/think>/g, "")
    .replace(/<\|?(?:turn|end_of_turn|im_end|eos|end)\|?>/g, "") // end-of-turn markers the host leaves in
    .trim();
  if (process.env.NPU_DEBUG) console.log(`  [npu] ${m.label} raw reply:\n${text}`);
  if (schema) text = salvageJson(text, schema);
  return { text, stats };
}
