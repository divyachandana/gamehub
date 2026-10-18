"""Typed data models shared across modules: configuration, observations, metrics and events.

Events are the contract between the game server and the browser. Every event is
serialized with ``model_dump(mode="json")`` and sent over Server-Sent Events.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from san_francisco.maze import Heading, Move

Device = Literal["NPU", "GPU", "HUMAN"]
Runtime = Literal["simulated", "qnn", "human"]
GridPoint = tuple[int, int]


# --------------------------------------------------------------------------- configuration


class SimProfile(BaseModel):
    """Behavior of a simulated racer (used until real models are wired in)."""

    model_config = ConfigDict(frozen=True)

    tps: float = Field(gt=0, description="Tokens per second while streaming")
    ttft_ms: float = Field(ge=0, description="Time to first token")
    accuracy: float = Field(ge=0, le=1, description="Chance of picking the optimal move")
    jitter: float = Field(default=0.15, ge=0, le=1, description="Random +/- spread on timings")
    reply_tokens: tuple[int, int] = Field(default=(22, 34), description="Token count range")


class RogueSpec(BaseModel):
    """A racer allowed to cheat (UNFAIR mode); Laya decides when."""

    model_config = ConfigDict(frozen=True)

    jumps: int = Field(default=3, ge=0, description="Hedge jumps per race")
    drones: int = Field(default=2, ge=0, description="Drone peeks per race")
    threshold: float = Field(default=0.4, ge=0, le=1, description="Laya yes-probability to jump")
    # Laya's drone answers run lower than its jump answers (about 0.17 when slightly unsure up
    # to 0.54 when lost with the rival close), so the drone gets its own, lower bar.
    drone_threshold: float = Field(default=0.15, ge=0, le=1, description="... to fly the drone")
    jump_penalty_s: float = Field(default=1.5, ge=0, description="Extra time to hop a hedge")
    drone_time_s: float = Field(default=2.0, ge=0, description="Time to fly the drone")
    min_jump_gain: int = Field(
        default=2, ge=1, description="Only offer jumps saving this many blocks"
    )


class RacerSpec(BaseModel):
    """One racer: a model on one compute unit, a simulated model, or the human player."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str
    device: Device
    runtime: Runtime
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    label: str | None = Field(default=None, max_length=10, description="Short tag, e.g. '1B GPU'")
    qnn_model: str | None = Field(default=None, description="Key in qnn.models (runtime: qnn)")
    max_tokens: int = Field(default=48, ge=8, le=512, description="Reply cap for real models")
    sim: SimProfile | None = None
    rogue: RogueSpec | None = None

    @property
    def display_label(self) -> str:
        """Short name shown on the leaderboard and podium (defaults to the device)."""
        return self.label or self.device


class RaceSettings(BaseModel):
    """Track and timing parameters shared by every racer."""

    model_config = ConfigDict(frozen=True)

    maze_width: int = Field(default=7, ge=2, le=20)
    maze_height: int = Field(default=7, ge=2, le=20)
    loop_ratio: float = Field(default=0.06, ge=0, le=1)
    branchiness: float = Field(default=0.6, ge=0, le=1)
    seed: int = 16
    segment_time_s: float = Field(default=1.4, gt=0, description="Drive time per road")
    uturn_penalty_s: float = Field(default=0.6, ge=0)
    max_decisions: int = Field(default=80, ge=1)
    countdown_s: int = Field(default=3, ge=0, le=10)
    token_event_interval_s: float = Field(default=0.1, gt=0)


class GameSpec(BaseModel):
    """One game in the hub (``games:`` in ``racers.yaml``): same racers, its own track."""

    model_config = ConfigDict(frozen=True)

    title: str
    tagline: str = ""
    maps: list[str] = Field(
        default_factory=list,
        description="Real street maps (scripts/fetch_map.py), relative to racers.yaml; "
        "empty = a random hedge maze of maze_width x maze_height",
    )


# --------------------------------------------------------------------------- street maps


class StreetLine(BaseModel):
    """One straight street of a real grid: a column (north-south) or a row (east-west)."""

    model_config = ConfigDict(frozen=True)

    name: str
    offset_m: float = Field(description="Meters from the map origin, across the street")


class CityBlock(BaseModel):
    """What stands on one block between two neighboring columns and rows."""

    model_config = ConfigDict(frozen=True)

    x: int = Field(description="Block east of column x (-1 = the ring west of the grid)")
    y: int = Field(description="Block north of row y (-1 = the ring south of the grid)")
    height_m: float = Field(ge=0, description="Typical building height, weighted by footprint")
    max_height_m: float = Field(ge=0)
    built: float = Field(ge=0, le=1, description="Share of the block covered by buildings")
    park: float = Field(ge=0, le=1, description="Share of the block covered by parks")
    name: str | None = Field(default=None, description="Best-known place on the block")


class SkylineBuilding(BaseModel):
    """A tall building outside the grid, seen on the horizon."""

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    lat: float
    lon: float
    height_m: float = Field(gt=0)
    width_m: float = Field(gt=0, description="Size along the map's east-west axis")
    depth_m: float = Field(gt=0, description="Size along the map's north-south axis")


class CityMap(BaseModel):
    """A real street grid made from OpenStreetMap data by ``scripts/fetch_map.py``."""

    model_config = ConfigDict(frozen=True)

    name: str
    title: str
    attribution: str
    origin: tuple[float, float] = Field(description="Latitude, longitude of the map's frame")
    rotation_deg: float = Field(description="Street grid angle, counterclockwise from east")
    columns: list[StreetLine] = Field(min_length=2, description="North-south, west to east")
    rows: list[StreetLine] = Field(min_length=2, description="East-west, south to north")
    streets: list[tuple[GridPoint, GridPoint]] = Field(
        description="Real roads between neighboring junctions"
    )
    start: GridPoint
    exit: GridPoint
    blocks: list[CityBlock]
    skyline: list[SkylineBuilding] = Field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="Area fetched: south, west, north, east"
    )
    tile_zooms: list[int] = Field(
        default_factory=list, description="Zoom levels of the area's map pictures in maps/tiles"
    )

    @property
    def width(self) -> int:
        """Junction columns."""
        return len(self.columns)

    @property
    def height(self) -> int:
        """Junction rows."""
        return len(self.rows)


# --------------------------------------------------------------------------- decisions


class OptionView(BaseModel):
    """What a driver can see about one road leaving the current junction."""

    move: Move
    compass: str
    visits: int = Field(ge=0, description="How often this racer already drove to that junction")


class Observation(BaseModel):
    """Everything a model is told at a junction (no full map: fog of war)."""

    node: GridPoint
    heading: Heading
    goal_dx: int = Field(description="Blocks to the exit, positive = east")
    goal_dy: int = Field(description="Blocks to the exit, positive = north")
    options: list[OptionView]


class DecisionMetrics(BaseModel):
    """Inference speed for one decision, timed on our side of the stream."""

    ttft_ms: float
    tps: float
    tokens: int
    latency_ms: float


class RacerStats(BaseModel):
    """Running totals for one racer, shown on the leaderboard."""

    decisions: int = 0
    wrong_turns: int = 0
    invalid_moves: int = 0
    dead_ends: int = 0
    cheats: int = 0
    tokens_total: int = 0
    segments_driven: int = 0
    avg_tps: float = 0.0
    avg_ttft_ms: float = 0.0
    finish_time_s: float | None = None
    place: int | None = None

    def record(self, metrics: DecisionMetrics) -> None:
        """Fold one decision's metrics into the running averages."""
        count = self.decisions + 1
        self.avg_tps += (metrics.tps - self.avg_tps) / count
        self.avg_ttft_ms += (metrics.ttft_ms - self.avg_ttft_ms) / count
        self.tokens_total += metrics.tokens
        self.decisions = count


# --------------------------------------------------------------------------- events


class _Event(BaseModel):
    t: float = Field(description="Seconds since the race was created (server clock)")


class MazeView(BaseModel):
    """Serializable maze layout for the frontend."""

    width: int
    height: int
    start: GridPoint
    exit: GridPoint
    roads: list[tuple[GridPoint, GridPoint]]
    dead_ends: list[GridPoint]
    optimal_path: list[GridPoint]
    seed: int
    closed: list[tuple[GridPoint, GridPoint]] = Field(
        default_factory=list, description="Real streets closed for this race"
    )
    city: CityMap | None = None


class RacerView(BaseModel):
    """Public racer info for the frontend (no secrets, no endpoints)."""

    id: str
    name: str
    label: str
    device: Device
    runtime: Runtime
    color: str
    heading: Heading
    rogue: bool = Field(default=False, description="Allowed to cheat (UNFAIR mode)")


class RaceInitEvent(_Event):
    type: Literal["race_init"] = "race_init"
    race_id: str
    preview: bool = Field(default=False, description="True = attract screen, race not started")
    game: str = Field(default="rally", description="Hub game this race belongs to")
    area: int = Field(default=0, ge=0, description="Which of the game's maps (if it has several)")
    areas: list[str] = Field(default_factory=list, description="Titles of the game's maps")
    maze: MazeView
    racers: list[RacerView]
    settings: RaceSettings


class CountdownEvent(_Event):
    type: Literal["countdown"] = "countdown"
    remaining: int


class RaceStartEvent(_Event):
    type: Literal["race_start"] = "race_start"


class ThinkingEvent(_Event):
    type: Literal["thinking"] = "thinking"
    racer: str
    node: GridPoint
    options: list[Move]


class TokenEvent(_Event):
    type: Literal["tokens"] = "tokens"
    racer: str
    tokens_decision: int
    tokens_total: int
    tps: float
    ttft_ms: float | None


class DecisionEvent(_Event):
    type: Literal["decision"] = "decision"
    racer: str
    move: Move
    why: str
    valid: bool
    optimal: bool
    metrics: DecisionMetrics
    stats: RacerStats


class DriveEvent(_Event):
    type: Literal["drive"] = "drive"
    racer: str
    src: GridPoint
    dst: GridPoint
    heading: Heading
    duration_s: float
    progress: float = Field(ge=0, le=1, description="1 = at the exit")
    jump: bool = Field(default=False, description="Hopped a hedge instead of driving a road")


class CheatEvent(_Event):
    """The rogue asked Laya about a cheat; ``used`` says whether it took it."""

    type: Literal["cheat"] = "cheat"
    racer: str
    kind: Literal["jump", "drone"]
    probability: float = Field(ge=0, le=1, description="Laya's yes-probability")
    used: bool
    laya_ms: float
    jumps_left: int
    drones_left: int
    stats: RacerStats


class IncidentEvent(_Event):
    type: Literal["incident"] = "incident"
    racer: str
    kind: Literal["wrong_turn", "dead_end", "invalid_move", "uturn", "offline", "timeout"]
    text: str


class FinishEvent(_Event):
    type: Literal["finish"] = "finish"
    racer: str
    place: int
    time_s: float
    stats: RacerStats


class RaceEndEvent(_Event):
    type: Literal["race_end"] = "race_end"
    results: dict[str, RacerStats]


RaceEvent = Annotated[
    RaceInitEvent
    | CountdownEvent
    | RaceStartEvent
    | ThinkingEvent
    | TokenEvent
    | DecisionEvent
    | DriveEvent
    | CheatEvent
    | IncidentEvent
    | FinishEvent
    | RaceEndEvent,
    Field(discriminator="type"),
]
