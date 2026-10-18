// Laya on the Snapdragon NPU: the stress meter's gut call.
//
// Laya (convaiinnovations/laya) is a ModernBERT decision model, not a chat model:
// one forward pass on the NPU (~0.3 s) gives a calibrated yes/no probability for
// a typed question. We ask it whether an answer sounds nervous, evasive or like
// a changed story, while Qwen3 writes the notes in parallel.
//
// Laya's host code needs PyTorch + transformers, so it runs in that Python as
// scripts/laya_bridge.py (JSON lines on stdin/stdout), the same bridge the racing
// games use for the rogue's cheats.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const LAYA_DIR = path.resolve(process.env.LAYA_DIR ?? path.join(here, "..", "..", "..", "npu", "laya"));
const PYTHON = process.env.LAYA_PYTHON ?? "C:/dev/et-venv/Scripts/python.exe";
const BRIDGE = path.join(here, "..", "scripts", "laya_bridge.py");
const START_TIMEOUT_MS = 90_000;

// Phrased in words: Laya barely reacts to numbers. Measured on sample answers:
// small talk ~0.16, a routine alibi ~0.21, cornered by evidence ~0.32, a changed story ~0.34.
const QUESTION = "Did this answer sound nervous, evasive or like a changed story?";
const CALM = 0.15;
const CRACKING = 0.35;

let bridge = null; // { proc, pending: [resolve...] }
let starting = null;
let disabled = false;

export function layaAvailable() {
  return !disabled && existsSync(PYTHON) && existsSync(path.join(LAYA_DIR, "laya_host.py"));
}

export function startLaya() {
  if (!layaAvailable()) return Promise.resolve(false);
  starting ??= new Promise((resolve) => {
    const started = Date.now();
    const proc = spawn(PYTHON, [BRIDGE, LAYA_DIR], { cwd: LAYA_DIR, stdio: ["pipe", "pipe", "ignore"], windowsHide: true });
    const pending = [];
    const timer = setTimeout(() => fail("didn't load in time"), START_TIMEOUT_MS);
    let failed = false;
    const fail = (why) => {
      if (failed) return;
      failed = true;
      clearTimeout(timer);
      console.warn(`  [npu] Laya unavailable (${why}); the note-taker rates stress instead.`);
      disabled = true;
      bridge = null;
      for (const p of pending.splice(0)) p(null);
      proc.kill();
      resolve(false);
    };
    proc.on("error", (err) => fail(err.message));
    proc.on("exit", (code) => fail(`exited with code ${code}`));
    readline.createInterface({ input: proc.stdout }).on("line", (line) => {
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        return;
      }
      if (msg.ready) {
        clearTimeout(timer);
        bridge = { proc, pending };
        console.log(`  [npu] Laya loaded in ${((Date.now() - started) / 1000).toFixed(1)}s`);
        return resolve(true);
      }
      pending.shift()?.(msg);
    });
  });
  return starting;
}

// Probability (0..1) that the answer sounds rattled, or null if Laya isn't running.
export async function rattled({ sensitiveAbout, question, reply }) {
  if (!(await startLaya()) || !bridge) return null;
  const state = `Sensitive about: ${sensitiveAbout ?? "unknown"}\nQuestion: ${question}\nAnswer: ${reply}`;
  const answer = await new Promise((resolve) => {
    bridge.pending.push(resolve);
    bridge.proc.stdin.write(`${JSON.stringify({ state, question: { type: "noul", instructions: QUESTION } })}\n`);
  });
  if (!answer || answer.error || typeof answer.noul !== "number") return null;
  return { p: answer.noul, npuMs: answer.npu_ms ?? null };
}

// New stress level: jumps up quickly when Laya hears cracks, drifts down slowly.
export function stressFrom(prev, p) {
  const target = Math.max(0, Math.min(100, ((p - CALM) / (CRACKING - CALM)) * 100));
  const next = target > prev ? prev + 0.7 * (target - prev) : prev - 0.3 * (prev - target);
  return Math.round(Math.max(0, Math.min(100, next)));
}
