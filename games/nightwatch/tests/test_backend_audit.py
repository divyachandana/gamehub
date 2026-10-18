"""UX-audit backend fixes: simulated-camera alerts say so, temperatures read cleanly, saving settings keeps
camera privacy choices and tonight's care records, and alerts carry their creation time."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
from engine import SCENARIOS, Engine  # noqa: E402
from scorer import RuleScorer  # noqa: E402

FACILITY = ROOT / "nightwatch" / "demo" / "facility.json"


def night() -> Engine:
    e = Engine(FACILITY, RuleScorer(), l1_timeout=0, ack_timeout=0)
    e.set_time("02:10")
    return e


def test_simulated_camera_alert_is_labelled_in_title_history_and_view() -> None:
    e = night()
    for _, action in SCENARIOS["camera_walkout"][1:]:   # skip the reset step, run the frames now
        action(e)
    a = e.alerts[0]
    assert a.simulated and a.title.endswith("(simulated camera)")
    assert "(simulated camera)" in a.history[0][1]
    v = e.view()["alerts"][0]
    assert v["simulated"] is True and isinstance(v["created"], float)
    e.sensor({"type": "door", "exit": "garden", "open": True})            # a real sensor afterwards
    assert not any(x.simulated for x in e.alerts if x.exit == "garden" and x.resident is None)


def test_real_alerts_are_not_simulated_and_created_is_epoch() -> None:
    e = night()
    e.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    v = e.view()["alerts"][0]
    assert v["simulated"] is False and abs(v["created"] - time.time()) < 5


def test_outside_temperature_has_no_trailing_zero() -> None:
    e = night()
    e.outside_temp_c = -3.0
    e.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})
    assert "-3 °C" in e.alerts[0].title and "-3.0" not in e.alerts[0].title


def test_saving_settings_keeps_cameras_records_and_clock(tmp_path: Path) -> None:
    e = night()
    e.set_camera("hall12", enabled=False, by="nurse")
    e.set_camera("front", schedule="night", by="nurse")
    e.cameras["hall12"]["doorway"] = {"line": [0.4, 0.1, 0.5, 0.9], "inside": 1}
    e.sensor({"type": "location", "resident": "R-07", "zone": "outside", "exit": "front"})
    e.resolve(1, "Found safe")                                               # draft record
    e.sensor({"type": "location", "resident": "R-21", "zone": "corridor"})   # alert 2 stays open
    clock = e.now()
    spec = e.settings()
    spec["facility"] = "Maple Wing (renamed)"
    spec.pop("timeouts")                                                     # this test engine uses 0 s
    out = e.apply_settings(spec, tmp_path / "facility.local.json")
    assert out == {"ok": True, "kept_alerts": 1, "draft_records_kept": 1, "open_alerts_cleared": 1}
    assert e.facility == "Maple Wing (renamed)"
    assert e.cameras["hall12"]["enabled"] is False                            # privacy choice survives
    assert e.cameras["front"]["schedule"] == "night"
    assert e.cameras["hall12"]["doorway"] == {"line": [0.4, 0.1, 0.5, 0.9], "inside": 1}
    assert [a.id for a in e.alerts] == [1] and e.alerts[0].record["status"] == "draft"
    assert abs(e.now() - clock) < 5 and e.is_night()
    assert any("turned OFF by nurse" in ev["text"] for ev in e.events)
    e.sensor({"type": "location", "resident": "R-03", "zone": "corridor"})
    assert e.alerts[-1].id == 3                                              # ids are never reused
    assert e.confirm_record(1, "Calm", "Reassurance", None, "", "nurse")["ok"]
