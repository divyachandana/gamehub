# Architecture

Everything runs on one Snapdragon X Elite laptop: a Python server per game, a browser UI, and the
models on the Hexagon NPU through ExecuTorch's QNN backend.

## 1. System

```mermaid
flowchart TB
  subgraph Browser["Browser"]
    hubpage["Hub page (hub/static)"]
    ui["Game UI — Three.js, Vite build<br/>3D chase cams · HUD · street map"]
  end

  subgraph Hub["hub/server.py :8000"]
    launcher["GameRunner<br/>one game at a time"]
  end

  subgraph Game["Game server :8001 (Inference Rally) or :8002 (San Francisco)"]
    sse["SSE event hub"]
    engine["Race engine<br/>junction → decide → drive"]
    maze["Maze / street map<br/>NetworkX + A*"]
    deciders["Deciders<br/>NPU · human · simulated"]
    rogue["Rogue cheats<br/>jump · drone"]
    cfg[("racers.yaml")]
  end

  subgraph NPU["Snapdragon X Elite — Hexagon NPU (QNN HTP v73)"]
    gemma["qnn_llm_host.exe<br/>Gemma-4-E2B .pte<br/>pipe rally_gemma"]
    qwen["qnn_llm_host.exe<br/>Qwen3-1.7B .pte<br/>pipe rally_qwen"]
    laya["laya_bridge.py → qnn_static_host.exe<br/>Laya .pte"]
  end

  hubpage -- "POST /api/play/{game}" --> launcher
  launcher -- "start / stop process" --> Game
  ui -- "POST start · stop · move" --> engine
  sse -- "Server-Sent Events" --> ui
  engine --- maze
  engine --- deciders
  engine --- rogue
  cfg --> deciders
  deciders -- "JSON lines over named pipe<br/>streamed tokens" --> gemma
  deciders -- "JSON lines over named pipe" --> qwen
  rogue -- "yes/no question (stdin/stdout)" --> laya
```

## 2. Processes and who starts them

```mermaid
flowchart LR
  run["run.ps1"] --> hubp["python hub/server.py"]
  hubp -- "pick Inference Rally" --> r["python -m inference_rally.server --port 8001"]
  hubp -- "pick San Francisco" --> s["python -m san_francisco.server --port 8002"]
  r & s -- "first start" --> h1["qnn_llm_host.exe (Gemma)"]
  r & s -- "first start" --> h2["qnn_llm_host.exe (Qwen3)"]
  r & s --> lb["laya_bridge.py (torch Python)"] --> sh["qnn_static_host.exe (Laya)"]
```

- Picking a game stops the other one first: each loads the NPU models, and the NPU is one device.
- The two `qnn_llm_host` processes outlive a stopped game; the next game finds their pipes and
  reuses them ("Using running NPU host"), so a switch doesn't reload Gemma or Qwen3.
- Laya's bridge belongs to its game and exits with it (its host code needs PyTorch, so it runs in a
  separate Python and talks JSON over stdin/stdout).
- Stopping the hub with Ctrl+C stops the game and asks both NPU hosts to shut down.

## 3. One junction decision

```mermaid
sequenceDiagram
  participant E as Race engine
  participant D as NPU decider
  participant H as qnn_llm_host (NPU)
  participant B as Browser
  E->>B: thinking (car stops at the junction)
  E->>D: prompt: heading, exit direction, roads (new / already driven)
  D->>H: {"prompt", "system_prompt", "reset": true, "stream": true, "seq_len"}
  H-->>D: {"delta": ...} first piece
  D-->>B: time to first token
  H-->>D: {"delta": ...} …
  D-->>B: live tokens/s, token count
  H-->>D: {"ok": true, "text", "metrics"}
  D->>E: {"move": "left", "why": "new road toward exit"}
  E->>E: parse + validate, compare with A*
  E->>B: decision, then drive to the next junction
```

- **Fresh context every junction:** `"reset": true` puts the KV cache back to position 0, so a race
  never fills the compiled context (exceeding it aborts the host).
- **Reply cap without a max-tokens flag:** the host stops at `seq_len` (prompt + reply), so the
  decider estimates the prompt's tokens from its length (learning the ratio from the host's own
  counts) and sets `seq_len` just above it.
- **Same prompt for every racer**, `temperature = 0`; Qwen3 gets `/no_think` (otherwise it reasons
  for ~300 tokens). The prompt marks roads as *new* or *already driven N×* and warns when the car is
  circling — that stopped the models looping.
- **Timing is ours:** time to first token and tokens/s are measured on the stream by the game, the
  same way for every racer.

## 4. The rogue (UNFAIR mode)

```mermaid
flowchart TD
  j["Rogue reaches a junction"] --> spot{"Code: is there a<br/>useful cheat?"}
  spot -- "hedge shortcut saves 2+ blocks" --> qj["Laya: 'Should the rogue taxi<br/>take this cheat now?'"]
  spot -- "circling (roads driven 2+ times)" --> qd["Laya: 'Should the taxi<br/>launch the drone now?'"]
  spot -- "no" --> drive["Ask its LLM as usual"]
  qj -- "p ≥ 0.4" --> jump["Hop over the hedge (+1.5 s)"]
  qd -- "p ≥ 0.15" --> drone["Drone scouts, take the best road (2 s)"]
  qj -- "below" --> drive
  qd -- "below" --> drive
```

Code does what Laya can't (geometry: which cheat helps); Laya gives the gut call as a calibrated
probability. The situation is described in words ("a huge shortcut", "completely lost", "the rival
is about to win") because Laya is a text classifier — with raw numbers its answers barely moved.

## 5. Events (server → browser)

`race_init` (maze or street map, racers) · `countdown` · `race_start` · `thinking` · `tokens` (live
speed) · `decision` (move, why, metrics) · `drive` (with `jump`) · `cheat` (Laya's probability) ·
`incident` · `finish` · `race_end`. A browser that reconnects gets the race's history replayed, so it
rebuilds the same scene.
