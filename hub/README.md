# Game Hub

A landing page that starts one of four separate apps; the three games run real models on the Snapdragon NPU:

| Game | Where | Port | Track |
|---|---|---|---|
| **Inference Rally** | [`games/inference-rally/`](../games/inference-rally/) | 8001 | A random hedge maze, new every race |
| **San Francisco** | [`games/san-francisco/`](../games/san-francisco/) | 8002 | Real San Francisco streets: North Beach, Alamo Square, Mission |
| **Alibi Breaker** | [`games/alibi-breaker/`](../games/alibi-breaker/) | 8003 | Detective game: interrogate AI suspects, break alibis on a real SF map |
| **NightWatch** | [`games/nightwatch/`](../games/nightwatch/) | 8004 | On-device night supervision for memory care, run as is (its own README) |

![The game hub](../docs/images/hub.jpg)

## Run

From the repo root, once: `.\setup.ps1` (Python env, the frontends, NPU checklist). Then:

```powershell
.\run.ps1      # or: .venv\Scripts\python hub\server.py   ->  http://localhost:8000
```

Pick a game (click it, or press **1**–**4**). The hub starts that game's server, waits until it
is up and opens it. Each game's **◀ HUB** button (or **H**) comes back here. **Ctrl+C** stops the hub, the running game and the NPU hosts.

## How it works

- `games.yaml` lists the games: title, picture, port, working folder and start command.
- Only one game runs at a time, because each one loads the NPU models (Gemma-4-E2B, Qwen3-1.7B and
  Laya). Picking a game stops the other one first.
- All games use the same NPU model hosts (`qnn_llm_host` on the named pipes `rally_gemma` and
  `rally_qwen`). A stopped game's hosts keep running and the next game reuses them, so switching
  doesn't reload the models. The hub shuts the hosts down when it exits.
- A game that is already running (started by hand on its port) is simply opened.
- Game logs go to `hub/logs/<game>.log`.

The pixel font is Press Start 2P (SIL Open Font License, `static/press-start-2p-LICENSE`).
