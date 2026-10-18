# Inference Rally — Coding Standards

Every file in this repo follows these rules. They keep the code readable,
testable and easy to change under hackathon time pressure.

## 1. Principles

- **Single responsibility** — one module, one job (`city.py` knows nothing about HTTP; `racers.py` knows nothing about the map).
- **Clear boundaries** — game logic ↔ inference ↔ transport (WebSocket) talk only through typed data models.
- **Config over code** — ports, models, colors, race settings live in `racers.yaml` / settings, never hard-coded.
- **Fail loudly, recover gracefully** — validate inputs at boundaries; a bad model answer is a counted mistake, never a crash.
- **Small functions** — aim for < 40 lines; if it needs a comment to explain *what* it does, split it.
- **No dead code** — delete experiments instead of commenting them out.
- **YAGNI** — build what the demo needs; stretch goals live in their own modules.

## 2. Python (backend)

| Topic | Rule |
|---|---|
| Version | Python 3.11+ |
| Style | PEP 8, enforced by **ruff** (lint) and **ruff format** (formatting), line length 100 |
| Types | Type hints on every function signature and public attribute; checked with **mypy** |
| Data models | **Pydantic** models (or frozen `dataclass`) for every message, config and event — no loose dicts across modules |
| Naming | `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants, `_leading_underscore` for private |
| Async | `async`/`await` for all I/O (HTTP, WebSocket); never block the event loop (no `time.sleep`, no sync HTTP) |
| Errors | Custom exceptions in `errors.py`; catch specific exceptions only, never bare `except:` |
| Logging | `logging` module with a module-level `logger = logging.getLogger(__name__)`; no `print` outside scripts |
| Imports | Absolute imports (`from inference_rally.city import CityGraph`), grouped stdlib / third-party / local |
| Constants | Magic numbers become named constants at the top of the module or settings |

### Comments & docstrings

- **Module docstring** at the top of every file: what the module is for, in 1–3 lines.
- **Google-style docstrings** on every public class and function: summary, `Args`, `Returns`, `Raises`.
- **Inline comments explain *why*, not *what*.** Good: `# Stream so we can measure TTFT ourselves; server-reported timings differ per runtime.` Bad: `# loop over racers`.
- `TODO(name): …` only with an owner; no orphan TODOs at demo time.

```python
"""Racer adapter: asks a model on the NPU for its move and measures inference speed."""

from __future__ import annotations

import logging
import time

from inference_rally.models import DecisionMetrics

logger = logging.getLogger(__name__)

STREAM_TIMEOUT_S = 10.0


async def decide(self, prompt: str) -> tuple[str, DecisionMetrics]:
    """Ask the racer's model for its next move and time the response.

    Args:
        prompt: Fully rendered decision prompt for the current intersection.

    Returns:
        The raw model reply and the metrics for this decision.

    Raises:
        RacerTimeoutError: If the model does not finish within STREAM_TIMEOUT_S.
    """
    # Measure TTFT on our side so every runtime is timed the same way.
    started = time.perf_counter()
    ...
```

## 3. JavaScript (frontend)

| Topic | Rule |
|---|---|
| Modules | ES modules, one concern per file (`scene.js`, `cars.js`, `hud.js`, `audio.js`, `ws.js`) |
| Style | **Prettier** formatting, **ESLint** recommended rules |
| Naming | `camelCase` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants |
| Docs | **JSDoc** on every exported function/class, including `@param` and `@returns` |
| State | Game state comes only from the server; the frontend renders, it does not decide |
| Magic values | Colors, speeds, camera settings in `config.js` |

## 4. Project hygiene

- **Tests** — `pytest` for game logic (`city`, `race`, `prompts`, `metrics`); inference is mocked with a fake endpoint so tests run offline.
- **Tooling** — `ruff check`, `ruff format`, `mypy`, `pytest` all pass before each commit (wired via `pre-commit`).
- **Dependencies** — pinned in `pyproject.toml` / `package.json`; no unused packages.
- **Git** — small commits with clear messages (`feat: add A* ghost racer`, `fix: handle invalid JSON move`).
- **Secrets / big files** — never committed; models (`.pte`) and assets downloads are git-ignored.
- **README** — every script in `scripts/` has a one-line usage note in the README.
