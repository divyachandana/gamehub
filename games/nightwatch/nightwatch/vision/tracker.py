"""Follow people across camera frames and detect when someone crosses a doorway line.

Detection alone answers "is someone in view?". The tracker answers the question the night
staff care about: "did someone walk OUT of that room?". It gives each person a stable id,
smooths their box, and watches their feet (bottom-centre of the box, the most stable point
for a doorway) cross a line the presenter draws on the camera page.

All coordinates are fractions of the frame (0..1). Nothing here stores images.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

CONFIRM_HITS = 3        # frames before a new track is trusted
MAX_MISSES = 8          # frames a track survives without a matching detection (occlusion)
SMOOTH = 0.6            # weight of the new box in the moving average (higher = snappier)
MATCH_IOU = 0.2
MATCH_DIST = 0.18       # fallback: feet within this distance count as the same person
START_SCORE = 0.5       # confidence needed to start a track ...
KEEP_SCORE = 0.3        # ... and the lower confidence that keeps an existing one alive
TRAIL = 20
CROSS_CONFIRM = 2       # frames on the new side before a crossing counts (no flicker at the line)
VEL_SMOOTH = 0.5        # how fast the velocity estimate follows new motion


@dataclass
class Track:
    id: int
    box: list[float]
    score: float
    hits: int = 1
    misses: int = 0
    trail: list[tuple[float, float]] = field(default_factory=list)
    side: int = 0          # +1 inside the room, -1 outside, 0 unknown/on the line
    vel: tuple[float, float] = (0.0, 0.0)   # box centre motion per frame (constant-velocity model)
    pending: int = 0       # side we may be crossing to ...
    pending_n: int = 0     # ... and for how many frames we've seen it

    def predicted(self) -> list[float]:
        """Where the box should be now, if the person kept moving (used for matching)."""
        k = self.misses + 1
        return [self.box[0] + self.vel[0] * k, self.box[1] + self.vel[1] * k,
                self.box[2] + self.vel[0] * k, self.box[3] + self.vel[1] * k]

    @property
    def feet(self) -> tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2, self.box[3])

    @property
    def confirmed(self) -> bool:
        return self.hits >= CONFIRM_HITS


@dataclass
class Doorway:
    """A line across the doorway. ``inside`` (+1/-1) says which side of the line is the room."""

    x1: float
    y1: float
    x2: float
    y2: float
    inside: int = 1

    def side(self, p: tuple[float, float], margin: float = 0.02) -> int:
        """+1 room side, -1 hallway side, 0 within ``margin`` of the line (no decision yet)."""
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        length = math.hypot(dx, dy) or 1.0
        signed = (dx * (p[1] - self.y1) - dy * (p[0] - self.x1)) / length
        if abs(signed) < margin:
            return 0
        return self.inside if signed > 0 else -self.inside

    def spans(self, p: tuple[float, float]) -> bool:
        """True if the point is alongside the doorway segment (not off past either end)."""
        dx, dy = self.x2 - self.x1, self.y2 - self.y1
        t = ((p[0] - self.x1) * dx + (p[1] - self.y1) * dy) / ((dx * dx + dy * dy) or 1.0)
        return -0.15 <= t <= 1.15


class Tracker:
    def __init__(self) -> None:
        self.tracks: list[Track] = []
        self.doorway: Doorway | None = None
        self._next = 1

    def set_doorway(self, line: list[float] | None, inside: int = 1) -> None:
        self.doorway = Doorway(*line, inside=1 if inside >= 0 else -1) if line else None
        for t in self.tracks:
            t.side = self.doorway.side(t.feet) if self.doorway else 0

    def update(self, detections: list[dict[str, float]]) -> list[dict[str, object]]:
        """Feed one frame's detections; return crossing events ({"track", "direction": "out"|"in"})."""
        dets = [d for d in detections if d["score"] >= KEEP_SCORE]
        unmatched = set(range(len(dets)))
        for t in sorted(self.tracks, key=lambda t: -t.hits):
            best, best_score = None, 0.0
            guess = t.predicted()
            guess_feet = ((guess[0] + guess[2]) / 2, guess[3])
            for i in unmatched:
                b = [dets[i]["x1"], dets[i]["y1"], dets[i]["x2"], dets[i]["y2"]]
                overlap = max(iou(guess, b), iou(t.box, b))
                near = 1 - min(math.dist(guess_feet, ((b[0] + b[2]) / 2, b[3])) / MATCH_DIST, 1)
                score = overlap if overlap >= MATCH_IOU else (0.1 * near if near > 0 else 0)
                if score > best_score:
                    best, best_score = i, score
            if best is None:
                t.misses += 1
                continue
            unmatched.discard(best)
            d = dets[best]
            old_c = ((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
            t.box = [SMOOTH * n + (1 - SMOOTH) * o for n, o in zip((d["x1"], d["y1"], d["x2"], d["y2"]), t.box)]
            new_c = ((t.box[0] + t.box[2]) / 2, (t.box[1] + t.box[3]) / 2)
            step = ((new_c[0] - old_c[0]) / (t.misses + 1), (new_c[1] - old_c[1]) / (t.misses + 1))
            t.vel = (VEL_SMOOTH * step[0] + (1 - VEL_SMOOTH) * t.vel[0], VEL_SMOOTH * step[1] + (1 - VEL_SMOOTH) * t.vel[1])
            t.score, t.hits, t.misses = d["score"], t.hits + 1, 0
        for i in unmatched:
            if dets[i]["score"] >= START_SCORE:
                t = Track(self._next, [dets[i]["x1"], dets[i]["y1"], dets[i]["x2"], dets[i]["y2"]], dets[i]["score"])
                t.side = self.doorway.side(t.feet) if self.doorway else 0
                self._next += 1
                self.tracks.append(t)
        self.tracks = [t for t in self.tracks if t.misses <= MAX_MISSES]

        events: list[dict[str, object]] = []
        for t in self.tracks:
            if t.misses == 0:
                t.trail = (t.trail + [t.feet])[-TRAIL:]
            if not (self.doorway and t.confirmed and t.misses == 0):
                continue
            side = self.doorway.side(t.feet)
            if side == 0 or not self.doorway.spans(t.feet):
                continue
            if not t.side:
                t.side = side
            elif side == t.side:
                t.pending, t.pending_n = 0, 0
            else:
                # Only believe a crossing once the feet stay on the new side for a couple of frames.
                t.pending_n = t.pending_n + 1 if t.pending == side else 1
                t.pending = side
                if t.pending_n >= CROSS_CONFIRM:
                    events.append({"track": t.id, "direction": "out" if side < 0 else "in"})
                    t.side, t.pending, t.pending_n = side, 0, 0
        return events

    def view(self) -> list[dict[str, object]]:
        return [{"id": t.id, "x1": t.box[0], "y1": t.box[1], "x2": t.box[2], "y2": t.box[3],
                 "score": round(t.score, 3), "confirmed": t.confirmed, "side": t.side,
                 "trail": [[round(x, 3), round(y, 3)] for x, y in t.trail]}
                for t in self.tracks if t.misses == 0]

    @property
    def people(self) -> int:
        return sum(1 for t in self.tracks if t.confirmed and t.misses == 0)


def iou(a: list[float], b: list[float]) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
