"""Care-record filing, backup requests, resident history, settings validation, laptop-only settings."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
from engine import TAXONOMY, Engine, validate_settings  # noqa: E402
from scorer import RuleScorer  # noqa: E402

FACILITY = ROOT / "nightwatch" / "demo" / "facility.json"


@pytest.fixture
def night() -> Engine:
    e = Engine(FACILITY, RuleScorer(), l1_timeout=999, ack_timeout=999)
    e.set_time("02:10")
    return e


def test_filing_a_care_record_uses_the_teams_pick_lists(night: Engine) -> None:
    night.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    a = night.alerts[0]
    assert night.confirm_record(a.id, "Calm", "Reassurance", None, "", "nurse")["error"]  # still open: no record yet
    night.resolve(a.id, "Found safe")
    assert a.record and a.record["status"] == "draft"
    assert "error" in night.confirm_record(a.id, "Happy", "Reassurance", None, "", "nurse")
    out = night.confirm_record(a.id, "Confused-disoriented", "Reorientation", "Movement", "walked back calmly", "Ana")
    assert out["ok"] and a.record["status"] == "filed" and a.record["confirmed_by"] == "Ana"
    assert night.report()["totals"]["records_filed"] == 1


def test_backup_raises_to_urgent_and_is_recorded(night: Engine) -> None:
    night.sensor({"type": "location", "resident": "R-07", "zone": "corridor"})
    a = night.alerts[0]
    night.request_backup(a.id, "Ana")
    assert a.level == 2 and a.backup_by == "Ana"
    assert any("Backup requested by Ana" in h for _, h in a.history)


def test_resident_history_is_sampled(night: Engine) -> None:
    for _ in range(11):
        night.tick()
    d = night.resident_detail("R-07")
    assert len(d["history"]) == 3 and d["now"]["zone"] == "room"
    assert "error" in night.resident_detail("nobody")


def test_settings_round_trip_and_validation(night: Engine, tmp_path: Path) -> None:
    spec = night.settings()
    spec["night_hours"] = [22, 6]
    spec["timeouts"] = {"l1": 30, "ack": 20}
    assert night.apply_settings(spec, tmp_path / "facility.local.json") == {"ok": True}
    assert night.night_start == 22 and night.l1_timeout == 30
    saved = json.loads((tmp_path / "facility.local.json").read_text())
    assert "timeouts" not in saved and saved["night_hours"] == [22, 6]
    bad = dict(spec, residents=spec["residents"] + [dict(spec["residents"][0])])
    with pytest.raises(ValueError, match="unique"):
        validate_settings(bad)
    with pytest.raises(ValueError, match="wander risk"):
        validate_settings(dict(spec, residents=[dict(spec["residents"][0], wander_risk=3)]))


def test_taxonomy_is_in_state(night: Engine) -> None:
    assert night.view()["taxonomy"] == TAXONOMY
