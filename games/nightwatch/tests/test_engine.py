"""Escalation rules: what fires, when it climbs, and what stays quiet."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
from engine import Engine  # noqa: E402
from scorer import RuleScorer  # noqa: E402

FACILITY = ROOT / "nightwatch" / "demo" / "facility.json"


@pytest.fixture
def night() -> Engine:
    e = Engine(FACILITY, RuleScorer(), l1_timeout=0, ack_timeout=0)
    e.set_time("02:10")
    return e


def open_alerts(e: Engine) -> list[tuple[int, str | None]]:
    return [(a.level, a.resident) for a in e.alerts if not a.resolved]


def test_leaving_room_at_night_is_a_heads_up(night: Engine) -> None:
    night.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    assert open_alerts(night) == [(1, "R-07")]


def test_ensuite_trip_stays_quiet(night: Engine) -> None:
    night.sensor({"type": "bed", "resident": "R-15", "in_bed": False})
    night.sensor({"type": "bed", "resident": "R-15", "in_bed": True})
    night.tick()
    assert open_alerts(night) == []


def test_exit_door_at_night_is_urgent(night: Engine) -> None:
    night.sensor({"type": "door", "exit": "front", "open": True})
    assert open_alerts(night)[0][0] == 2


def test_unanswered_alert_climbs_to_emergency_and_contacts(night: Engine) -> None:
    night.sensor({"type": "door", "exit": "front", "open": True})
    night.tick()
    a = night.alerts[0]
    assert a.level == 3
    assert any("supervisor" in h or "emergency alarm" in h for _, h in a.history)


def test_acknowledged_alert_stops_climbing(night: Engine) -> None:
    night.sensor({"type": "door", "exit": "front", "open": True})
    night.ack(night.alerts[0].id, "test")
    night.tick()
    assert night.alerts[0].level == 2


def test_outside_is_emergency_with_severity_3_record(night: Engine) -> None:
    night.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})
    a = night.alerts[0]
    assert a.level == 3
    night.resolve(a.id, "Found safe")
    assert a.record and a.record["severity"] == 3 and a.record["severity_label"] == "escalation"
    assert a.record["location_type"] == "Exterior (left the building)"


def test_found_safe_returns_resident_and_pauses_heads_ups(night: Engine) -> None:
    night.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    night.resolve(night.alerts[0].id, "Found safe")
    night.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    assert open_alerts(night) == []


def test_day_door_is_logged_as_a_visit() -> None:
    e = Engine(FACILITY, RuleScorer(), l1_timeout=0, ack_timeout=0)
    e.set_time("14:00")
    e.sensor({"type": "door", "exit": "front", "open": True})
    assert e.visits_today == 1 and open_alerts(e) == []


def test_camera_needs_three_frames_then_flags_room_exit(night: Engine) -> None:
    night.camera("hall12", 1, 5.0)
    night.camera("hall12", 1, 5.0)
    assert open_alerts(night) == []
    night.camera("hall12", 1, 5.0)
    assert open_alerts(night) == [(1, "R-07")]


def test_camera_at_entrance_is_urgent(night: Engine) -> None:
    for _ in range(3):
        night.camera("front", 1, 5.0)
    assert open_alerts(night)[0][0] == 2


def test_doorway_crossing_out_flags_and_back_in_clears(night: Engine) -> None:
    night.camera("hall12", 1, 5.0, [{"track": 1, "direction": "out"}], doorway=True)
    assert open_alerts(night) == [(1, "R-07")]
    night.camera("hall12", 1, 5.0, [{"track": 1, "direction": "in"}], doorway=True)
    assert open_alerts(night) == []
    assert night.alerts[0].resolved == "Went back into their room (camera)"


def test_with_a_doorway_presence_alone_does_not_fire(night: Engine) -> None:
    for _ in range(5):
        night.camera("hall12", 1, 5.0, [], doorway=True)
    assert open_alerts(night) == []


def test_camera_off_drops_events_and_is_logged(night: Engine) -> None:
    night.set_camera("hall12", enabled=False, by="test")
    assert night.camera_active("hall12") == (False, "turned off")
    assert any("turned OFF by test" in ev["text"] for ev in night.events)
    night.set_camera("hall12", enabled=True)
    assert night.camera_active("hall12")[0]


def test_night_only_camera_is_paused_during_the_day() -> None:
    e = Engine(FACILITY, RuleScorer(), l1_timeout=0, ack_timeout=0)
    e.set_time("14:00")
    e.set_camera("hall12", schedule="night")
    assert e.camera_active("hall12") == (False, "night only (21:00-05:00)")
    e.set_time("22:00")
    assert e.camera_active("hall12")[0]
