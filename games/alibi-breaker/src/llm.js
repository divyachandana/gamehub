// One function, two "brains", three backends.
//
//   complete({ tier: "big" | "fast", system, messages, schema? })
//
// "big"  = the suspect (role-play, needs to be good at staying in character)
// "fast" = the note-taker (pulls places/times out of answers, rates stress)
//
// Swap backends with env vars only - nothing else in the game knows which
// model is running. See .env.example.

import Anthropic from "@anthropic-ai/sdk";

const DEFAULT_MODELS = {
  claude: { big: "claude-opus-5-5", fast: "claude-haiku-5-5" },
  ollama: { big: "gemma3:4b", fast: "gemma3:1b" },
  // Any OpenAI-compatible local server: LM Studio, Nexa, Foundry Local,
  // llama.cpp, AnythingLLM... (e.g. Gemma + Laya on a Snapdragon X laptop).
  local: { big: "gemma-3-4b", fast: "laya" },
  mock: { big: "scripted-suspect", fast: "regex-notes" },
};

export class LLMError extends Error {}

function pickBaseProvider() {
  if (process.argv.includes("--mock")) return "mock";
  if (process.env.PROVIDER) return process.env.PROVIDER.trim().toLowerCase();
  if (process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN) return "claude";
  return "mock";
}

function tierConfig(tier, base) {
  const upper = tier.toUpperCase();
  const provider = (process.argv.includes("--mock") ? "mock" : process.env[`${upper}_PROVIDER`] ?? base)
    .trim()
    .toLowerCase();
  if (!DEFAULT_MODELS[provider]) {
    throw new Error(`Unknown provider "${provider}". Use claude, ollama, local or mock.`);
  }
  const model = provider === "mock" ? DEFAULT_MODELS.mock[tier] : process.env[`${upper}_MODEL`] ?? DEFAULT_MODELS[provider][tier];
  return { provider, model };
}

const base = pickBaseProvider();
export const config = { big: tierConfig("big", base), fast: tierConfig("fast", base) };

export async function complete({ tier, system, messages, schema, maxTokens = 4000, mock }) {
  const { provider, model } = config[tier];
  let text;
  if (provider === "mock") {
    const out = mock();
    return schema ? out : String(out);
  } else if (provider === "claude") {
    text = await claudeComplete({ model, system, messages, schema, maxTokens });
  } else if (provider === "ollama") {
    text = await ollamaComplete({ model, system, messages, schema });
  } else {
    text = await localComplete({ model, system, messages, schema, maxTokens });
  }
  return schema ? parseJson(text) : text.trim();
}

// Local models can take a minute to load on the first request. Load them at
// startup so the first question in a demo is fast.
export async function warmUp() {
  for (const tier of ["big", "fast"]) {
    const { provider, model } = config[tier];
    if (provider !== "ollama" && provider !== "local") continue;
    if (tier === "fast" && provider === config.big.provider && model === config.big.model) continue;
    const started = Date.now();
    try {
      await complete({ tier, system: "Reply with the word ok.", messages: [{ role: "user", content: "ok" }], maxTokens: 16 });
      console.log(`  Warmed up ${model} in ${((Date.now() - started) / 1000).toFixed(1)}s`);
    } catch (err) {
      console.warn(`  Warm-up failed for ${model}: ${err.message}`);
    }
  }
}

// ---------------------------------------------------------------- Claude

let anthropic;
// Models that accept server-side refusal fallbacks ("default" routing).
const FALLBACK_MODELS = /^claude-(opus-5|opus-5-5|fable-5-1|sonnet-5-5)$/;

async function claudeComplete({ model, system, messages, schema, maxTokens }) {
  const params = {
    model,
    max_tokens: maxTokens,
    system,
    messages,
    // Interrogation is a chat: keep it snappy.
    output_config: { effort: "low" },
  };
  if (schema) params.output_config.format = { type: "json_schema", schema };

  let response;
  try {
    anthropic ??= new Anthropic();
    response = FALLBACK_MODELS.test(model)
      ? await anthropic.beta.messages.create({
          ...params,
          betas: ["server-side-fallback-2026-07-01"],
          fallbacks: "default",
        })
      : await anthropic.messages.create(params);
  } catch (err) {
    if (err instanceof Anthropic.AuthenticationError) {
      throw new LLMError("Claude API key is missing or invalid. Put ANTHROPIC_API_KEY in .env (or run with PROVIDER=mock).");
    } else if (err instanceof Anthropic.RateLimitError) {
      throw new LLMError("Claude rate limit hit - wait a few seconds and ask again.");
    } else if (err instanceof Anthropic.APIConnectionError) {
      throw new LLMError("Can't reach the Claude API. Check your internet connection.");
    } else if (err instanceof Anthropic.APIError) {
      throw new LLMError(`Claude API error ${err.status}: ${err.message}`);
    } else if (err instanceof Anthropic.AnthropicError) {
      throw new LLMError(`Claude client error: ${err.message}`);
    }
    throw err;
  }

  if (response.stop_reason === "refusal") {
    throw new LLMError("The model declined to answer that question. Try rephrasing.");
  }
  return response.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("");
}

// ---------------------------------------------------------------- Ollama

async function ollamaComplete({ model, system, messages, schema }) {
  const url = (process.env.OLLAMA_URL ?? "http://localhost:11434").replace(/\/$/, "");
  let res;
  try {
    res = await fetch(`${url}/api/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model,
        stream: false,
        keep_alive: "30m", // don't unload the model mid-demo
        messages: [{ role: "system", content: system }, ...messages],
        ...(schema && { format: schema }),
        options: { temperature: schema ? 0.1 : 0.8 },
      }),
    });
  } catch {
    throw new LLMError(`Can't reach Ollama at ${url}. Is it running? Try: ollama serve`);
  }
  if (!res.ok) {
    const body = await res.text();
    if (res.status === 404) throw new LLMError(`Ollama doesn't have model "${model}". Try: ollama pull ${model}`);
    throw new LLMError(`Ollama error ${res.status}: ${body.slice(0, 200)}`);
  }
  const data = await res.json();
  // Some local reasoning models inline their thoughts - strip them.
  return (data.message?.content ?? "").replace(/<think>[\s\S]*?<\/think>/g, "");
}

// ------------------------------------------- OpenAI-compatible local server

async function localComplete({ model, system, messages, schema, maxTokens }) {
  const url = (process.env.LOCAL_URL ?? "http://localhost:1234/v1").replace(/\/$/, "");
  const body = {
    model,
    messages: [{ role: "system", content: system }, ...messages],
    temperature: schema ? 0.1 : 0.8,
    max_tokens: maxTokens,
  };
  if (schema) {
    body.response_format = { type: "json_schema", json_schema: { name: "notes", strict: true, schema } };
  }
  const post = (payload) =>
    fetch(`${url}/chat/completions`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(process.env.LOCAL_API_KEY && { authorization: `Bearer ${process.env.LOCAL_API_KEY}` }),
      },
      body: JSON.stringify(payload),
    });

  let res;
  try {
    res = await post(body);
    // Some local servers don't support json_schema - retry with a plain request.
    if (!res.ok && schema && res.status === 400) {
      const { response_format, ...plain } = body;
      plain.messages = [
        { role: "system", content: `${system}\n\nReply with only a JSON object matching this schema:\n${JSON.stringify(schema)}` },
        ...messages,
      ];
      res = await post(plain);
    }
  } catch {
    throw new LLMError(`Can't reach the local model server at ${url}. Is it running?`);
  }
  if (!res.ok) {
    throw new LLMError(`Local server error ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  const data = await res.json();
  return (data.choices?.[0]?.message?.content ?? "").replace(/<think>[\s\S]*?<\/think>/g, "");
}

// ---------------------------------------------------------------- helpers

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    // Small local models sometimes wrap JSON in prose or ``` fences.
    const match = text.match(/\{[\s\S]*\}/);
    if (match) {
      try {
        return JSON.parse(match[0]);
      } catch {}
    }
    throw new LLMError("Note-taker returned unreadable JSON.");
  }
}
