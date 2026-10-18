"""HTTP game server: race controls plus a Server-Sent Events stream for the browser.

Endpoints:
    GET  /api/events       SSE stream of race events (replays the current race on connect)
    POST /api/race/start   start a new race; optional JSON body {"seed": int, "profile": str}
    POST /api/race/stop    stop the current race
    POST /api/race/preview show a profile's maze + racers on the attract screen, stopping any
                           running race {"profile": str, "seed": int}
    POST /api/human/move   the human player's move at a junction {"racer": str, "move": str}
    GET  /api/health       liveness check

Run with ``python -m inference_rally.server`` (see README for options).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from inference_rally import laya, qnn_host
from inference_rally.config import AppConfig, build_decider, load_config
from inference_rally.deciders import HumanDecider
from inference_rally.errors import ConfigError
from inference_rally.maze import Move, generate_maze
from inference_rally.models import RaceEvent
from inference_rally.race import CheatJudge, RaceEngine

logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
SSE_PING_S = 15
SUBSCRIBER_QUEUE_SIZE = 2000


class _UiFiles(StaticFiles):
    """The built frontend; ``index.html`` is always revalidated.

    The JS/CSS bundles have content hashes in their names, but the page that points to them
    doesn't: a browser that kept an old ``index.html`` would keep running the old game.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        if response.media_type == "text/html":
            response.headers["Cache-Control"] = "no-cache"
        return response


class EventHub:
    """Fan-out of race events to every connected browser.

    The hub keeps the current race's history, so a browser that connects (or
    reconnects after a Wi-Fi blip) mid-race rebuilds the exact same scene.
    """

    def __init__(self) -> None:
        """Create an empty hub."""
        self._history: list[dict[str, object]] = []
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()

    def reset(self) -> None:
        """Forget the previous race (called right before a new one starts)."""
        self._history.clear()

    def publish(self, event: RaceEvent) -> None:
        """Record ``event`` and push it to every subscriber."""
        payload = event.model_dump(mode="json")
        self._history.append(payload)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A stalled tab must not slow the race down for everyone else.
                logger.warning("Dropping slow SSE subscriber")
                self._subscribers.discard(queue)

    async def subscribe(self) -> AsyncIterator[dict[str, object]]:
        """Yield the current race history, then live events until the client leaves."""
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        for payload in self._history:
            queue.put_nowait(payload)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)


class RaceController:
    """Owns the single running race and its background task."""

    def __init__(self, config: AppConfig, hub: EventHub) -> None:
        """Bind the controller to a config and an event hub."""
        self._config = config
        self._hub = hub
        self._task: asyncio.Task[object] | None = None
        self._humans: dict[str, HumanDecider] = {}

    def _build_engine(self, seed: int | None, profile: str | None) -> RaceEngine:
        settings = self._config.race
        if seed is not None:
            settings = settings.model_copy(update={"seed": seed})
        maze = generate_maze(
            settings.maze_width,
            settings.maze_height,
            settings.seed,
            loop_ratio=settings.loop_ratio,
            branchiness=settings.branchiness,
        )
        racers = [
            (spec, build_decider(spec, maze, settings.seed, self._config.qnn))
            for spec in self._config.racers(profile)
        ]
        self._humans = {spec.id: d for spec, d in racers if isinstance(d, HumanDecider)}
        judge = _laya_judge(self._config) if any(s.rogue for s, _ in racers) else None
        return RaceEngine(maze, racers, settings, self._hub.publish, judge)

    async def preview(self, profile: str | None = None, seed: int | None = None) -> None:
        """Show a profile's maze and racers on the attract screen without racing.

        Also how a race is stopped from the UI: the running race is cancelled and the
        same maze (``seed``) is shown again, ready for a restart.
        """
        engine = self._build_engine(seed=seed, profile=profile)
        await self.stop()
        self._hub.reset()
        engine.announce(preview=True)

    def human_move(self, racer_id: str, move: Move) -> bool:
        """Forward the player's move; False if it is not their turn or the move is illegal."""
        human = self._humans.get(racer_id)
        return bool(human and human.submit(move))

    async def start(self, seed: int | None = None, profile: str | None = None) -> str:
        """Stop any running race and start a new one.

        Returns:
            The new race id.

        Raises:
            ConfigError: If the profile or a racer runtime is invalid.
        """
        engine = self._build_engine(seed, profile)  # validate config before stopping
        await self.stop()
        self._hub.reset()
        self._task = asyncio.create_task(engine.run(), name=f"race-{engine.race_id}")
        self._task.add_done_callback(_log_task_failure)
        logger.info("Started race %s", engine.race_id)
        return engine.race_id

    async def stop(self) -> None:
        """Cancel the running race, if any."""
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None


def _log_task_failure(task: asyncio.Task[object]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Race task crashed", exc_info=task.exception())


def create_app(config: AppConfig) -> Starlette:
    """Build the Starlette application.

    Args:
        config: Parsed ``racers.yaml``.
    """
    hub = EventHub()
    controller = RaceController(config, hub)

    async def events(_: Request) -> EventSourceResponse:
        async def stream() -> AsyncIterator[dict[str, str]]:
            async for payload in hub.subscribe():
                yield {"data": json.dumps(payload, separators=(",", ":"))}

        return EventSourceResponse(stream(), ping=SSE_PING_S)

    async def start_race(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            race_id = await controller.start(seed=body.get("seed"), profile=body.get("profile"))
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"race_id": race_id})

    async def preview_race(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            await controller.preview(
                profile=body.get("profile"),  # type: ignore[arg-type]
                seed=body.get("seed"),  # type: ignore[arg-type]
            )
        except ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    async def human_move(request: Request) -> JSONResponse:
        body = await _json_body(request)
        try:
            move = Move(str(body.get("move")))
        except ValueError:
            return JSONResponse({"error": "move must be left|straight|right|back"}, status_code=400)
        accepted = controller.human_move(str(body.get("racer")), move)
        return JSONResponse({"accepted": accepted})

    async def stop_race(_: Request) -> JSONResponse:
        await controller.stop()
        return JSONResponse({"stopped": True})

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "profile": config.active_profile})

    routes: list[Route | Mount] = [
        Route("/api/events", events),
        Route("/api/race/start", start_race, methods=["POST"]),
        Route("/api/race/stop", stop_race, methods=["POST"]),
        Route("/api/race/preview", preview_race, methods=["POST"]),
        Route("/api/human/move", human_move, methods=["POST"]),
        Route("/api/health", health),
    ]
    # Serve the built frontend when present, so the demo needs a single process.
    if FRONTEND_DIST.is_dir():
        routes.append(Mount("/", _UiFiles(directory=FRONTEND_DIST, html=True), name="ui"))

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await controller.preview()
        npu_loading = asyncio.create_task(_prestart_npu_hosts(config))
        yield
        npu_loading.cancel()
        await controller.stop()  # don't leave a race task running after Ctrl+C
        await asyncio.to_thread(qnn_host.close_all)  # free the NPU
        await asyncio.to_thread(laya.close_all)

    return Starlette(routes=routes, lifespan=lifespan)


async def _prestart_npu_hosts(config: AppConfig) -> None:
    """Load the NPU models while the attract screen shows, so the first race starts fast."""
    specs = [s for p in config.profiles.values() for s in p]
    if config.laya is not None and any(s.rogue for s in specs):
        try:
            await asyncio.to_thread(laya.get_bridge(config.laya).start)
        except laya.LayaError:
            logger.warning("Laya did not start", exc_info=True)
    if config.qnn is None:
        return
    keys = {s.qnn_model for p in config.profiles.values() for s in p if s.runtime == "qnn"}
    for key in sorted(k for k in keys if k and k in config.qnn.models):
        try:
            await asyncio.to_thread(qnn_host.get_host(config.qnn, key).start)
        except qnn_host.QnnHostError:
            logger.warning("NPU host for %s did not start", key, exc_info=True)


def _laya_judge(config: AppConfig) -> CheatJudge | None:
    """Laya's yes-probability for a cheat question, asked off the event loop."""
    if config.laya is None:
        return None
    bridge = laya.get_bridge(config.laya)

    async def judge(state: str, question: str) -> float:
        return await asyncio.to_thread(bridge.noul, state, question)

    return judge


async def _json_body(request: Request) -> dict[str, object]:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Inference Rally game server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--profile", help="Override active_profile from racers.yaml")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = load_config()
    if args.profile:
        config = config.model_copy(update={"active_profile": args.profile})
    uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
