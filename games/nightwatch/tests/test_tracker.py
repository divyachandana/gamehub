"""Tracking across frames and doorway-line crossing."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
from vision.tracker import Tracker  # noqa: E402


def person(cx: float, feet_y: float, score: float = 0.9) -> dict[str, float]:
    return {"x1": cx - 0.08, "y1": feet_y - 0.4, "x2": cx + 0.08, "y2": feet_y, "score": score}


def walk(tr: Tracker, points: list[tuple[float, float]]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for cx, fy in points:
        events += tr.update([person(cx, fy)])
    return events


def vertical_doorway() -> Tracker:
    # Doorway is a vertical line at x = 0.5; the room is on the left (x < 0.5).
    tr = Tracker()
    tr.set_doorway([0.5, 0.3, 0.5, 1.0], inside=1)
    if tr.doorway and tr.doorway.side((0.3, 0.8)) != 1:
        tr.set_doorway([0.5, 0.3, 0.5, 1.0], inside=-1)
    return tr


def test_walking_out_of_the_room_is_one_out_event() -> None:
    tr = vertical_doorway()
    events = walk(tr, [(0.25 + 0.05 * i, 0.85) for i in range(10)])
    assert [e["direction"] for e in events] == ["out"]


def test_walking_back_in_is_an_in_event() -> None:
    tr = vertical_doorway()
    events = walk(tr, [(0.75 - 0.05 * i, 0.85) for i in range(10)])
    assert [e["direction"] for e in events] == ["in"]


def test_walking_along_the_hallway_never_crosses() -> None:
    tr = vertical_doorway()
    assert walk(tr, [(0.7, 0.5 + 0.04 * i) for i in range(10)]) == []


def test_track_keeps_its_id_through_a_short_occlusion() -> None:
    tr = Tracker()
    for i in range(4):
        tr.update([person(0.3 + 0.01 * i, 0.8)])
    first = tr.view()[0]["id"]
    for _ in range(3):
        tr.update([])
    tr.update([person(0.36, 0.8)])
    assert tr.view()[0]["id"] == first


def test_low_confidence_cannot_start_a_track_but_keeps_one_alive() -> None:
    tr = Tracker()
    tr.update([person(0.3, 0.8, score=0.35)])
    assert tr.tracks == []
    tr.update([person(0.3, 0.8, score=0.9)])
    tr.update([person(0.31, 0.8, score=0.35)])
    assert len(tr.tracks) == 1 and tr.tracks[0].hits == 2


def test_standing_on_the_line_and_wobbling_does_not_fire() -> None:
    tr = vertical_doorway()
    walk(tr, [(0.3, 0.85), (0.35, 0.85), (0.4, 0.85), (0.45, 0.85)])
    # Feet jitter across the line for single frames only.
    events = walk(tr, [(0.53, 0.85), (0.47, 0.85), (0.53, 0.85), (0.47, 0.85), (0.46, 0.85)])
    assert events == []


def test_fast_walker_keeps_one_id_through_a_gap() -> None:
    tr = Tracker()
    for i in range(5):
        tr.update([person(0.1 + 0.07 * i, 0.8)])
    first = tr.view()[0]["id"]
    tr.update([])
    tr.update([])
    tr.update([person(0.1 + 0.07 * 7, 0.8)])   # moved 0.21 while unseen: matched by predicted position
    assert [t["id"] for t in tr.view()] == [first]
