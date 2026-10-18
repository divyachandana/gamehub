# Alibi Breaker

A detective game where AI suspects lie, and a map proves it.

You question suspects. Every place and time they mention is pinned on a real San Francisco street map (OpenStreetMap). When someone's story is physically impossible ("I was at Joe's Café until 9:10 and home by 9:15", but the walk takes 18 minutes), or they claim to have been somewhere "the whole time" while a receipt puts them across the neighborhood, the game flags it. Catch the liar, then accuse.

It ships with two cases:

| Case | You are | The lie |
|---|---|---|
| **The Missing Demo Laptop** | Detective | ExecuTorch Hackathon at Founders, Inc.: Team Watchout Guardian AI's Copilot+ PC vanishes overnight. The suspects are the three judges, Adam, Derrick and Nick. |
| **The Fort Mason Demo Day Theft** | Detective | A prototype taken at Pier 2, next to the Founders, Inc. campus; one subject "never left" Building C |
| **The North Beach Heist** 💎 | Detective | A diamond thief's alibi doesn't fit the walk down Stockton Street |
| **The Valencia Street Hit-and-Run Claim** 🚗 | Insurance claims investigator | A claimant "never left dinner", but his card bought ice by Dolores Park |

## How it works: two brains plus a map

```
You ask a question
   │
   ▼
BIG model ── plays the suspect (knows the secret, tells the cover story)
   │
   ▼
FAST model ── note-taker: pulls out {place, time}, stress level, a body-language tell
   │
   ▼
Plain code ── checks every claim against the map + evidence (no AI, so it's always right)
   │
   ▼
🚨 "Marco said Joe's Café until 9:10 and Kestrel Court at 9:15. The walk takes 18 min."
```

Two safety checks keep the game fair: a claim only counts if the suspect actually named that place and said that time in the answer. Small models sometimes invent places, and this stops them from framing an innocent person.

## Run it

You need Node.js 20+.

```bash
npm install
npm start
```

Open http://localhost:3000.

With no `.env` file it runs in **mock mode**: scripted answers, no AI. Use it to test the UI, or as a backup if a model dies during a demo:

```bash
npm run mock
```

## On the Snapdragon NPU (the game hub)

In the [game hub](../../hub/README.md) the game starts with `--npu`, and all three AI jobs run on the
laptop's Hexagon NPU through ExecuTorch + QNN, with the same models and model hosts as the racing games:

| Job | Model on the NPU | How |
|---|---|---|
| The suspect (big brain) | **Gemma-4-E2B** | `qnn_llm_host` on the pipe `rally_gemma`; the interview so far is folded into each one-shot prompt and trimmed to the 4k context |
| The note-taker (fast brain) | **Qwen3-1.7B** (`/no_think`) | `qnn_llm_host` on the pipe `rally_qwen`; returns the notes JSON, repaired if the small model breaks it |
| The stress meter | **Laya** (ModernBERT) | one forward pass: *did this answer sound nervous, evasive or like a changed story?*, run in parallel with the notes |

![Interrogating the judges on the NPU](../../docs/images/alibi-race.jpg)

Each answer shows its NPU timing (time to first token, tokens/s, Laya's ms). If a model host is
already running (another game started it), the game reuses it; otherwise it starts it. On its own,
after the repo's `.\setup.ps1`:

```powershell
cd games\alibi-breaker
node server.js --npu --port 8003     # http://localhost:8003
```

`src/npu.js` talks to the model hosts. `src/laya.js` runs Laya through `scripts/laya_bridge.py` in a
Python with torch + transformers (`LAYA_PYTHON` in `.env`, set by `setup.ps1`). Paths default to the
repo's `npu/` folder; override them with `NPU_DIR`, `LAYA_DIR` and `QNN_SDK_ROOT`. `NPU_DEBUG=1`
prints the raw model replies.

## Pick your models

Copy `.env.example` to `.env` and choose one setup (or `PROVIDER=npu`, above):

**Claude (cloud)**
```
PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...
```
This defaults to `claude-opus-5-5` for the suspect and `claude-haiku-5-5` for the note-taker. Requests to Opus use server-side refusal fallbacks (`fallbacks: "default"`), so if a safety classifier declines a line, another model answers instead.

**Gemma + Laya on the Snapdragon X laptop (fully local)**

Most local model apps (LM Studio, Nexa, AnythingLLM, Foundry Local, llama.cpp) expose an OpenAI-compatible server. Point the game at it:
```
PROVIDER=local
LOCAL_URL=http://localhost:1234/v1   # whatever port your app shows
BIG_MODEL=gemma-3-4b                 # exact model id from your app
FAST_MODEL=laya                      # exact model id from your app
```
To find the exact model ids: `curl http://localhost:1234/v1/models`

**Ollama**
```
PROVIDER=ollama
BIG_MODEL=gemma3:4b
FAST_MODEL=your-fast-model
```

**Mix and match:** run each brain on a different backend:
```
BIG_PROVIDER=local
BIG_MODEL=gemma-3-4b
FAST_PROVIDER=ollama
FAST_MODEL=qwen2.5:7b
```

The terminal prints which models are running, and so does the start screen. Local models are warmed up at startup, because the first load can take about a minute.

## Write a new case

1. Copy a file in `cases/`, give it a new `id` (must match the filename), and edit it.
2. Click **Change case** in the game. Cases are re-read every time a new game starts, so you don't need to restart.

Key fields:

| Field | What it is |
|---|---|
| `map` | Which street map in `maps/` to use (`sf-fort-mason`, `sf-north-beach`, `sf-mission`, `sf-alamo-square`) |
| `places` | The case's places, each on a real corner (`"corner": ["Stockton Street", "Union Street"]`) or at exact coordinates for off-grid spots (`"at": [37.80676, -122.43149]`, linked to the nearest corner by walking distance) |
| `paths` | Optional footpaths between nearby off-grid places, e.g. `[["pier2", "cafe"]]` |
| `labels` | Wording: "Detective" vs "Claims investigator", "Accuse" vs "Flag as fraud" |
| `crime` | Where and when the incident happened (shown on the map) |
| `watch` | Optional. A place and time window. A suspect's route passing through it gets flagged as suspicious. |
| `suspects[].truth` | What really happened. Only the suspect model sees this. |
| `suspects[].cover` | The story they tell |
| `suspects[].pressure` | How they change the story when cornered |
| `suspects[].guilty` | Exactly one suspect is `true` |
| `evidence[].facts` | Hard facts: `{ suspect, place, time }`. The checker uses these. |
| `suspects[].mock` | Scripted lines for mock mode |

**Design tip:** write the guilty suspect's `cover` so it's impossible on the map, and give innocent suspects stories that fit the walking times. Innocent suspects should still hide *something* (a forgotten alarm, a skipped shift). That makes them nervous, which makes them good red herrings.

If a case file has a mistake (an unknown place id, a bad time format), the game tells you exactly what's wrong when you open it.

## The maps

The street maps come from the [Inference Rally](https://github.com/divyachandana/Inference-Rally) project, built from OpenStreetMap: a 7×7 grid of real named streets per neighborhood, plus offline map tiles in `maps/tiles`. Walking time is 80 m per minute along real streets (`src/map.js`).

To add a neighborhood, fetch it with Inference Rally's `scripts/fetch_map.py`, then convert it:

```bash
node scripts/import-rally-map.mjs ../Inference-Rally/maps/sf-my-area.json
```

and copy its new tiles from `Inference-Rally/maps/tiles` into `maps/tiles`. Map data © OpenStreetMap contributors (ODbL).

## Files

```
server.js          HTTP server + API
src/llm.js         the model switch (claude / ollama / local / npu / mock)
src/npu.js         Gemma-4-E2B + Qwen3-1.7B on the NPU (ExecuTorch qnn_llm_host pipes)
src/laya.js        Laya on the NPU: the stress meter
src/game.js        prompts, one interrogation turn, the alibi checker
src/map.js         street graph + shortest walking time
src/case.js        loads and validates case files
src/mock.js        scripted suspects + regex note-taker (no AI)
cases/*.json       the cases
maps/*.json        street maps (corners + streets), maps/tiles = map pictures
scripts/           import-rally-map.mjs (Inference Rally map -> Alibi Breaker map)
public/            the game UI
```
