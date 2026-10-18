"""check.py: web pages/assets present, and tonight's saved state is reported (or flagged unreadable)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
import check  # noqa: E402
from engine import Engine  # noqa: E402
from scorer import RuleScorer  # noqa: E402


def test_web_pages_and_assets_exist(capsys: pytest.CaptureFixture[str]) -> None:
    check.check_web()
    assert "FAIL" not in capsys.readouterr().out


def test_state_file_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(check, "LOGS", tmp_path)
    check.check_state()
    assert "none saved yet" in capsys.readouterr().out
    e = Engine(ROOT / "nightwatch" / "demo" / "facility.json", RuleScorer(), 20, 15)
    e.set_time("02:10")
    e.sensor({"type": "door", "exit": "front", "open": True})
    (tmp_path / "night_state.json").write_text(json.dumps(e.snapshot()))
    check.check_state()
    out = capsys.readouterr().out
    assert "1 alerts (1 open), will be restored" in out
    (tmp_path / "night_state.json").write_text("{broken")
    check.check_state()
    assert "unreadable" in capsys.readouterr().out
