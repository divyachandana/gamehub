"""Race engine: runs every racer's junction → decide → drive loop concurrently.

The engine is real time on purpose: a racer's car waits at a junction for exactly as
long as its model takes to answer, so faster inference visibly wins.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from san_francisco.deciders import Decider
from san_francisco.errors import DeciderError, DecisionTimeoutError, InvalidMoveError
from san_francisco.maze import Heading, Maze, Move, Node
from san_francisco.metrics import TokenMeter
from san_francisco.models import (
    CheatEvent,
    CityMap,
    CountdownEvent,
    DecisionEvent,
    DecisionMetrics,
    DriveEvent,
    FinishEvent,
    IncidentEvent,
    MazeView,
    Observation,
    OptionView,
    RaceEndEvent,
    RaceEvent,
    RaceInitEvent,
    RacerSpec,
    RacerStats,
    RacerView,
    RaceSettings,
    RaceStartEvent,
    ThinkingEvent,
    TokenEvent,
)
from san_francisco.prompts import build_messages, parse_move
from san_francisco.rogue import CheatPlan, plan_cheat

logger = logging.getLogger(__name__)

# Publishing is synchronous on purpose: events reach subscribers in exactly the order
# they happen (a late live-token update can never overtake its decision).
EmitFn = Callable[[RaceEvent], None]
# Answers a yes/no question about a situation with a probability (Laya on the NPU).
CheatJudge = Callable[[str, str], Awaitable[float]]


_FAILURE_WHY = {"invalid_move": "invalid reply", "timeout": "too slow", "offline": "model offline"}
_FAILURE_TEXT = {
    "invalid_move": "Invalid answer!",
    "timeout": "Too slow!",
    "offline": "Model offline!",
}
WARMUP_TIMEOUT_S = 90.0


@dataclass(frozen=True)
class Decision:
    """Outcome of one junction: the move taken and, if it was a fallback, why."""

    move: Move
    why: str
    metrics: DecisionMetrics
    failure: str | None  # None = the decider's own valid answer


@dataclass
class RacerState:
    """Mutable per-racer state during one race."""

    spec: RacerSpec
    decider: Decider
    node: Node
    heading: Heading
    visits: Counter[Node] = field(default_factory=Counter)
    stats: RacerStats = field(default_factory=RacerStats)
    jumps_left: int = 0
    drones_left: int = 0


class RaceEngine:
    """Orchestrates one race from countdown to podium."""

    def __init__(
        self,
        maze: Maze,
        racers: list[tuple[RacerSpec, Decider]],
        settings: RaceSettings,
        emit: EmitFn,
        judge: CheatJudge | None = None,
        city: CityMap | None = None,
        game: str = "rally",
        area: int = 0,
        areas: list[str] | None = None,
    ) -> None:
        """Prepare a race.

        Args:
            maze: The track shared by all racers.
            racers: Each racer's spec with the decider that drives it.
            settings: Timing and limits.
            emit: Function that publishes an event (e.g. to the SSE hub).
            judge: Decides whether a rogue racer cheats; without one, nobody cheats.
            city: The real street map the maze was carved from, if any.
            game: Hub game this race belongs to.
            area: Which of the game's maps this race runs on.
            areas: Titles of the game's maps.
        """
        self.race_id = uuid.uuid4().hex[:8]
        self._maze = maze
        self._city = city
        self._game = game
        self._area = area
        self._areas = areas or []
        self._settings = settings
        self._emit = emit
        self._judge = judge
        self._clock_start = time.perf_counter()
        self._finish_order: list[str] = []
        start_heading = maze.initial_heading()
        self._racers = [
            RacerState(spec=spec, decider=decider, node=maze.start, heading=start_heading)
            for spec, decider in racers
        ]
        for racer in self._racers:
            racer.visits[maze.start] += 1
            if racer.spec.rogue:
                racer.jumps_left = racer.spec.rogue.jumps
                racer.drones_left = racer.spec.rogue.drones

    def now(self) -> float:
        """Seconds since the race was created, rounded for compact events."""
        return round(time.perf_counter() - self._clock_start, 3)

    def announce(self, preview: bool = False) -> None:
        """Publish the track and racers (``preview=True`` for the attract screen)."""
        self._emit(self._init_event(preview))

    async def run(self) -> dict[str, RacerStats]:
        """Run the whole race and return final stats keyed by racer id."""
        self.announce()
        await self._warm_up()
        for remaining in range(self._settings.countdown_s, 0, -1):
            self._emit(CountdownEvent(t=self.now(), remaining=remaining))
            await asyncio.sleep(1)
        self._emit(RaceStartEvent(t=self.now()))

        await asyncio.gather(*(self._run_racer(racer) for racer in self._racers))

        results = {racer.spec.id: racer.stats for racer in self._racers}
        self._emit(RaceEndEvent(t=self.now(), results=results))
        return results

    async def _warm_up(self) -> None:
        """Load every model before the countdown, so the first junction is a fair fight.

        A racer whose model cannot be reached still races (with fallback moves) and is
        flagged "offline" instead of blocking everyone else.
        """

        async def warm(racer: RacerState) -> None:
            try:
                await asyncio.wait_for(racer.decider.warmup(), timeout=WARMUP_TIMEOUT_S)
            except (DeciderError, asyncio.TimeoutError):
                logger.warning("Racer %s is offline", racer.spec.id, exc_info=True)
                self._incident(racer, "offline", "Model offline!")

        await asyncio.gather(*(warm(racer) for racer in self._racers))

    # ----------------------------------------------------------------------- racer loop

    async def _run_racer(self, racer: RacerState) -> None:
        while racer.node != self._maze.exit:
            if racer.spec.rogue and await self._try_cheat(racer):
                continue
            if self._maze.is_junction(racer.node, racer.heading):
                if racer.stats.decisions >= self._settings.max_decisions:
                    logger.info("Racer %s hit the decision limit (DNF)", racer.spec.id)
                    return
                decision = await self._decide(racer)
                self._record_decision(racer, decision)
                move = decision.move
            else:
                # No real choice (corridor or dead end): drive on without asking the
                # model, so every model call is a meaningful decision.
                move = self._only_move(racer)
            await self._drive(racer, move)

        self._finish_order.append(racer.spec.id)
        racer.stats.place = len(self._finish_order)
        racer.stats.finish_time_s = self.now()
        self._emit(
            FinishEvent(
                t=self.now(),
                racer=racer.spec.id,
                place=racer.stats.place,
                time_s=racer.stats.finish_time_s,
                stats=racer.stats,
            )
        )

    async def _decide(self, racer: RacerState) -> Decision:
        """Ask the racer's decider for a move; fall back to a safe move if it misbehaves."""
        options = self._maze.options(racer.node, racer.heading)
        obs = self._observe(racer, options)
        self._emit(
            ThinkingEvent(t=self.now(), racer=racer.spec.id, node=racer.node, options=list(options))
        )

        last_sent = 0.0
        tokens_before = racer.stats.tokens_total

        def on_token(meter: TokenMeter) -> None:
            # Throttle: one live update per interval is plenty for the HUD bar.
            nonlocal last_sent
            now = self.now()
            if now - last_sent < self._settings.token_event_interval_s:
                return
            last_sent = now
            self._emit(
                TokenEvent(
                    t=now,
                    racer=racer.spec.id,
                    tokens_decision=meter.tokens,
                    tokens_total=tokens_before + meter.tokens,
                    tps=round(meter.tps, 1),
                    ttft_ms=None if meter.ttft_ms is None else round(meter.ttft_ms),
                )
            )

        started = time.perf_counter()
        try:
            result = await racer.decider.decide(build_messages(obs), obs, on_token)
        except DecisionTimeoutError:
            return self._fallback(racer, options, started, "timeout")
        except DeciderError:
            logger.warning(
                "Decider for %s failed; using fallback move", racer.spec.id, exc_info=True
            )
            return self._fallback(racer, options, started, "offline")

        try:
            move, why = parse_move(result.text, list(options))
        except InvalidMoveError:
            logger.warning("Invalid reply from %s: %r", racer.spec.id, result.text[:80])
            fallback = self._fallback(racer, options, started, "invalid_move")
            return Decision(fallback.move, fallback.why, result.metrics, "invalid_move")
        return Decision(move, why, result.metrics, None)

    def _fallback(
        self, racer: RacerState, options: dict[Move, Node], started: float, failure: str
    ) -> Decision:
        """Deterministic safe move when the decider gave no usable answer."""
        move = min(options, key=lambda m: (racer.visits[options[m]], m is Move.BACK))
        waited_ms = round((time.perf_counter() - started) * 1000, 1)
        metrics = DecisionMetrics(ttft_ms=waited_ms, tps=0, tokens=0, latency_ms=waited_ms)
        return Decision(move, _FAILURE_WHY[failure], metrics, failure)

    def _record_decision(self, racer: RacerState, decision: Decision) -> None:
        """Score a junction decision and announce it (plus any incident)."""
        dst = self._maze.options(racer.node, racer.heading)[decision.move]
        optimal = self._maze.is_optimal_step(racer.node, dst)

        racer.stats.record(decision.metrics)
        if decision.failure:
            racer.stats.invalid_moves += 1
        if not optimal:
            racer.stats.wrong_turns += 1

        self._emit(
            DecisionEvent(
                t=self.now(),
                racer=racer.spec.id,
                move=decision.move,
                why=decision.why,
                valid=decision.failure is None,
                optimal=optimal,
                metrics=decision.metrics,
                stats=racer.stats,
            )
        )
        if decision.failure:
            self._incident(racer, decision.failure, _FAILURE_TEXT[decision.failure])
        elif not optimal:
            self._incident(racer, "wrong_turn", "Wrong turn!")

    async def _drive(self, racer: RacerState, move: Move) -> None:
        """Drive one road segment in real time and update the racer's position."""
        src = racer.node
        dst = self._maze.options(src, racer.heading)[move]
        racer.heading = Heading.between(src, dst)
        racer.node = dst
        racer.visits[dst] += 1
        racer.stats.segments_driven += 1

        duration = self._settings.segment_time_s
        if move is Move.BACK:
            duration += self._settings.uturn_penalty_s
            self._incident(racer, "uturn", "U-turn!")
        self._emit(
            DriveEvent(
                t=self.now(),
                racer=racer.spec.id,
                src=src,
                dst=dst,
                heading=racer.heading,
                duration_s=duration,
                progress=self._progress(dst),
            )
        )
        await asyncio.sleep(duration)

        if self._maze.is_dead_end(dst):
            racer.stats.dead_ends += 1
            self._incident(racer, "dead_end", "Dead end!")

    # ----------------------------------------------------------------------- cheating

    async def _try_cheat(self, racer: RacerState) -> bool:
        """Offer the rogue a useful cheat, let Laya decide, and carry it out.

        Returns:
            True if the rogue cheated (and has already moved), False to drive normally.
        """
        rogue = racer.spec.rogue
        if rogue is None or self._judge is None:
            return False
        plan = plan_cheat(
            self._maze,
            racer.node,
            racer.heading,
            racer.visits,
            rogue,
            racer.jumps_left,
            racer.drones_left,
            self._rival_distance(),
        )
        if plan is None:
            return False

        started = time.perf_counter()
        try:
            probability = await self._judge(plan.state, plan.question)
        except DeciderError:
            logger.warning("Laya failed; no more cheats this race", exc_info=True)
            self._judge = None
            self._incident(racer, "offline", "Laya offline!")
            return False
        threshold = rogue.drone_threshold if plan.kind == "drone" else rogue.threshold
        used = probability >= threshold
        if used:
            racer.stats.cheats += 1
            if plan.kind == "jump":
                racer.jumps_left -= 1
            else:
                racer.drones_left -= 1
        self._emit(
            CheatEvent(
                t=self.now(),
                racer=racer.spec.id,
                kind=plan.kind,
                probability=round(probability, 3),
                used=used,
                laya_ms=round((time.perf_counter() - started) * 1000, 1),
                jumps_left=racer.jumps_left,
                drones_left=racer.drones_left,
                stats=racer.stats,
            )
        )
        if used:
            await self._cheat(racer, plan, rogue.jump_penalty_s, rogue.drone_time_s)
        return used

    async def _cheat(
        self, racer: RacerState, plan: CheatPlan, jump_penalty_s: float, drone_time_s: float
    ) -> None:
        if plan.kind == "drone":
            # The drone shows the best road; the car follows it without asking its model.
            await asyncio.sleep(drone_time_s)
            options = self._maze.options(racer.node, racer.heading)
            move = min(options, key=lambda m: self._maze.distance_to_exit(options[m]))
            await self._drive(racer, move)
            return
        assert plan.target is not None
        src = racer.node
        racer.heading = Heading.between(src, plan.target)
        racer.node = plan.target
        racer.visits[plan.target] += 1
        racer.stats.segments_driven += 1
        duration = self._settings.segment_time_s + jump_penalty_s
        self._emit(
            DriveEvent(
                t=self.now(),
                racer=racer.spec.id,
                src=src,
                dst=plan.target,
                heading=racer.heading,
                duration_s=duration,
                progress=self._progress(plan.target),
                jump=True,
            )
        )
        await asyncio.sleep(duration)

    def _rival_distance(self) -> int | None:
        """Road blocks between the leading honest racer still on track and the exit."""
        honest = [
            self._maze.distance_to_exit(r.node)
            for r in self._racers
            if r.spec.rogue is None and r.node != self._maze.exit
        ]
        return min(honest, default=None)

    def _only_move(self, racer: RacerState) -> Move:
        """The single sensible move where there is no choice: ahead, or back at a dead end."""
        options = self._maze.options(racer.node, racer.heading)
        forward = [move for move in options if move is not Move.BACK]
        return forward[0] if forward else Move.BACK

    # ----------------------------------------------------------------------- helpers

    def _observe(self, racer: RacerState, options: dict[Move, Node]) -> Observation:
        return Observation(
            node=racer.node,
            heading=racer.heading,
            goal_dx=self._maze.exit[0] - racer.node[0],
            goal_dy=self._maze.exit[1] - racer.node[1],
            options=[
                OptionView(
                    move=move,
                    compass=racer.heading.turn(move).compass,
                    visits=racer.visits[dst],
                )
                for move, dst in options.items()
            ],
        )

    def _progress(self, node: Node) -> float:
        """Share of the optimal route already covered (0 at start, 1 at the exit).

        Clamped at 0 because a detour can take a car farther from the exit than the start.
        """
        total = self._maze.distance_to_exit(self._maze.start)
        if not total:
            return 1.0
        return round(max(0.0, 1 - self._maze.distance_to_exit(node) / total), 3)

    def _incident(self, racer: RacerState, kind: str, text: str) -> None:
        self._emit(
            IncidentEvent(t=self.now(), racer=racer.spec.id, kind=kind, text=text)  # type: ignore[arg-type]
        )

    def _init_event(self, preview: bool) -> RaceInitEvent:
        maze = self._maze
        return RaceInitEvent(
            t=self.now(),
            race_id=self.race_id,
            preview=preview,
            game=self._game,
            area=self._area,
            areas=self._areas,
            maze=MazeView(
                width=maze.width,
                height=maze.height,
                start=maze.start,
                exit=maze.exit,
                roads=maze.roads,
                dead_ends=maze.dead_ends(),
                optimal_path=maze.shortest_path(),
                seed=maze.seed,
                closed=sorted(set(self._city.streets) - set(maze.roads)) if self._city else [],
                city=self._city,
            ),
            racers=[
                RacerView(
                    id=r.spec.id,
                    name=r.spec.name,
                    label=r.spec.display_label,
                    device=r.spec.device,
                    runtime=r.spec.runtime,
                    color=r.spec.color,
                    heading=r.heading,
                    rogue=r.spec.rogue is not None,
                )
                for r in self._racers
            ],
            settings=self._settings,
        )
