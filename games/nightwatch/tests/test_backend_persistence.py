"""Surviving a restart mid-night: tonight's state is saved atomically and restored only if it's from tonight."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
from engine import Engine  # noqa: E402
from scorer import RuleScorer  # noqa: E402

FACILITY = ROOT / "nightwatch" / "demo" / "facility.json"


def engine() -> Engine:
    return Engine(FACILITY, RuleScorer(), l1_timeout=0, ack_timeout=0)


def busy_night(tmp: Path) -> Engine:
    e = engine()
    e._state_path = tmp / "night_state.json"
    e.set_time("02:10", 2)
    e.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})      # alert 1: heads-up
    e.sensor({"type": "door", "exit": "front", "open": True})                   # escalates alert 1 to urgent
    e.ack(1, "Night nurse")
    e.sensor({"type": "location", "resident": "R-21", "zone": "outside", "exit": "garden"})   # alert 2: emergency
    e.resolve(2, "Found safe")
    e.confirm_record(2, "Calm", "Reassurance", None, "walked back", "Night nurse")
    e.set_camera("hall12", enabled=False, by="test")
    e.set_camera("front", schedule="night", by="test")
    e.cameras["hall12"]["doorway"] = {"line": [0.4, 0.1, 0.5, 0.9], "inside": -1}
    assert e.save_now()
    return e


def test_restart_brings_back_alerts_events_cameras_and_clock(tmp_path: Path) -> None:
    before = busy_night(tmp_path)
    data = json.loads((tmp_path / "night_state.json").read_text())
    after = engine()
    ok, why = after.restore(data)
    assert ok, why
    a1, a2 = after.alerts
    b1 = before.alerts[0]
    assert (a1.id, a1.level, a1.max_level, a1.acked_by, a1.resolved) == (b1.id, b1.level, b1.max_level, "Night nurse", None)
    assert a1.history == before.alerts[0].history and isinstance(a1.history[0], tuple)
    assert a2.resolved == "Found safe" and a2.went_outside and a2.record["status"] == "filed"
    assert after.cameras["hall12"]["enabled"] is False and after.cameras["front"]["schedule"] == "night"
    assert after.cameras["hall12"]["doorway"] == {"line": [0.4, 0.1, 0.5, 0.9], "inside": -1}
    assert after.exits["front"].open and after.residents["R-07"].zone != "room"
    assert after.speed == 2 and abs(after.now() - before.now()) < 5
    assert any("restored tonight's state" in ev["text"] for ev in after.events)
    assert [ev for ev in after.events][1:] == list(before.events)
    after.sensor({"type": "location", "resident": "R-03", "zone": "corridor"})
    assert after.alerts[-1].id == 3                                            # ids keep counting, never reused


def test_open_heads_up_is_not_auto_closed_by_restart(tmp_path: Path) -> None:
    e = engine()
    e._state_path = tmp_path / "s.json"
    e.set_time("02:10")
    e.sensor({"type": "bed", "resident": "R-07", "in_bed": False})
    e.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    e.save_now()
    after = engine()
    assert after.restore(json.loads((tmp_path / "s.json").read_text()))[0]
    after.tick()
    assert not after.alerts[0].resolved                                         # resident still up, alert still open


def test_old_or_foreign_state_is_not_restored(tmp_path: Path) -> None:
    data = busy_night(tmp_path).snapshot()
    assert engine().restorable(data)[0]
    assert not engine().restorable(data, now=float(data["saved_at"]) + 30 * 3600)[0]
    assert not engine().restore({**data, "version": 99})[0]
    assert not engine().restore({**data, "resident_ids": ["X-1"]})[0]
    e = engine()
    ok, why = e.restore({**data, "alerts": [{"id": 1}]})                         # broken alert -> clean night
    assert not ok and "unreadable" in why and e.alerts == []


def test_saves_are_atomic_and_debounced(tmp_path: Path) -> None:
    e = engine()
    path = tmp_path / "night_state.json"
    e.start_autosave(path, debounce=0.2)
    try:
        e.set_time("02:10")
        for _ in range(20):
            e.sensor({"type": "hr", "resident": "R-07", "bpm": 80})
        deadline = time.time() + 3
        while not path.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert json.loads(path.read_text())["version"] == 1
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        e.stop_autosave()
    assert json.loads(path.read_text())["rev"] == e.rev                        # final save on stop has everything


def test_scenario_reset_clears_saved_state(tmp_path: Path) -> None:
    e = busy_night(tmp_path)
    e.reset()
    e.save_now()
    data = json.loads((tmp_path / "night_state.json").read_text())
    assert data["alerts"] == [] and data["cameras"]["hall12"]["enabled"] is True
