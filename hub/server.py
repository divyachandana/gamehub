r"""Game hub: a landing page on :8000 that starts the game you pick.

Inference Rally (repo root) and San Francisco (san_francisco/) are separate games with their own
servers. Only one runs at a time: both load the NPU models, so picking a game stops the other
first. They share the NPU model hosts (qnn_llm_host on named pipes), which outlive a stopped game
server, so a switch doesn't reload the models; the hub shuts the hosts down when it exits.

Run with the Python that has both games installed (see hub/README.md):

    .venv\Scripts\python hub\server.py

Endpoints:
    GET  /                 the hub page
    GET  /api/games        the games, and which one is running
    POST /api/play/{game}  stop the other game, start this one, return its address
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from pydantic import BaseModel, Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

logger = logging.getLogger("hub")

HUB_DIR = Path(__file__).resolve().parent
STATIC_DIR = HUB_DIR / "static"
LOG_DIR = HUB_DIR / "logs"
START_TIMEOUT_S = 90.0  # first start loads the NPU models; later ones reuse them
HEALTH_POLL_S = 0.5
STOP_TIMEOUT_S = 10.0


class GameEntry(BaseModel):
    """One game the hub can start (``games:`` in ``hub/games.yaml``)."""

    title: str
    tagline: str = ""
    detail: str = ""
    image: str
    accent: str = "#ffd23f"
    port: int = Field(ge=1024, le=65535)
    cwd: str = Field(description="Working directory, relative to games.yaml")
    command: list[str] = Field(description='"python" means the Python running the hub')


class HubConfig(BaseModel):
    """``hub/games.yaml``."""

    port: int = 8000
    games: dict[str, GameEntry]
    npu_pipes: list[str] = Field(default_factory=list)


class GameRunner:
    """Starts and stops the game servers so that at most one runs at a time."""

    def __init__(self, config: HubConfig) -> None:
        """Bind to the hub config; no game runs until one is picked."""
        self._config = config
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._lock = asyncio.Lock()

    def running(self) -> str | None:
        """Return the game whose server is up (started by the hub or by hand), if any."""
        return next((name for name in self._config.games if self._healthy(name)), None)

    async def play(self, name: str) -> None:
        """Make ``name`` the running game: stop the other one, start this one if needed.

        Raises:
            RuntimeError: If the game's server does not come up in time.
        """
        async with self._lock:
            for other in self._config.games:
                if other != name:
                    await asyncio.to_thread(self._stop, other)
            if not self._healthy(name):
                await asyncio.to_thread(self._start, name)

    def close(self) -> None:
        """Stop every game the hub started and the NPU model hosts they share."""
        for name in list(self._procs):
            self._stop(name)
        for pipe in self._config.npu_pipes:
            _shutdown_pipe(pipe)

    # --------------------------------------------------------------------- internals

    def _start(self, name: str) -> None:
        game = self._config.games[name]
        command = [sys.executable if part == "python" else part for part in game.command]
        LOG_DIR.mkdir(exist_ok=True)
        log = (LOG_DIR / f"{name}.log").open("ab")
        logger.info("Starting %s: %s", name, " ".join(command))
        self._procs[name] = subprocess.Popen(
            command, cwd=HUB_DIR / game.cwd, stdout=log, stderr=subprocess.STDOUT
        )
        log.close()  # the child keeps its own handle
        deadline = time.monotonic() + START_TIMEOUT_S
        while not self._healthy(name):
            if self._procs[name].poll() is not None:
                code = self._procs.pop(name).returncode
                raise RuntimeError(f"{game.title} exited with code {code}; see hub/logs/{name}.log")
            if time.monotonic() > deadline:
                raise RuntimeError(f"{game.title} did not start in time; see hub/logs/{name}.log")
            time.sleep(HEALTH_POLL_S)
        logger.info("%s is up on port %d", name, game.port)

    def _stop(self, name: str) -> None:
        proc = self._procs.pop(name, None)
        if proc is None or proc.poll() is not None:
            return
        logger.info("Stopping %s", name)
        # The NPU model hosts it started keep running (and are reused by the next game);
        # Laya's bridge exits on its own when its parent is gone.
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _healthy(self, name: str) -> bool:
        url = f"http://127.0.0.1:{self._config.games[name].port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310 - localhost
                return bool(response.status == 200)
        except (urllib.error.URLError, OSError):
            return False


def _shutdown_pipe(name: str) -> None:
    """Ask a qnn_llm_host serving ``name`` to exit (it answers one client at a time)."""
    try:
        with open(rf"\\.\pipe\{name}", "r+b", buffering=0) as pipe:
            pipe.write(b'{"cmd": "shutdown"}\n')
            pipe.read(1)
        logger.info("Shut down NPU host %s", name)
    except OSError:
        pass  # not running


def create_app(config: HubConfig) -> Starlette:
    """Build the hub app."""
    runner = GameRunner(config)

    async def index(_: Request) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def games(_: Request) -> JSONResponse:
        running = await asyncio.to_thread(runner.running)
        return JSONResponse(
            {
                "running": running,
                "games": {
                    name: game.model_dump(include={"title", "tagline", "detail", "image", "accent"})
                    for name, game in config.games.items()
                },
            }
        )

    async def play(request: Request) -> JSONResponse:
        name = request.path_params["game"]
        if name not in config.games:
            return JSONResponse({"error": f"Unknown game {name!r}"}, status_code=404)
        try:
            await runner.play(name)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        host = request.url.hostname or "localhost"
        return JSONResponse({"url": f"http://{host}:{config.games[name].port}/"})

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        yield
        await asyncio.to_thread(runner.close)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/games", games),
            Route("/api/play/{game}", play, methods=["POST"]),
            Mount("/", StaticFiles(directory=STATIC_DIR), name="static"),
        ],
        lifespan=lifespan,
    )


def load_config(path: Path = HUB_DIR / "games.yaml") -> HubConfig:
    """Read ``hub/games.yaml``."""
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return HubConfig.model_validate(raw)


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Game hub: Inference Rally and San Francisco")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, help="Override port from hub/games.yaml")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = load_config()
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(
            create_app(config), host=args.host, port=args.port or config.port, log_level="warning"
        )


if __name__ == "__main__":
    main()
