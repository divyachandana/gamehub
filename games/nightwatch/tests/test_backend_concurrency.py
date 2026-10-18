"""Many threads at once (sensors, acks, resolves, camera frames, settings of cameras, readers) while the
1 s tick and the autosave run: no exceptions, alert ids unique, and an open alert's level never drops."""
import random
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
import server  # noqa: E402
from engine import Engine  # noqa: E402
from pairing import Pairing  # noqa: E402
from scorer import RuleScorer  # noqa: E402

RESIDENTS = ["R-03", "R-07", "R-15", "R-21"]
ZONES = ["room", "corridor", "common", "exit"]


class FakeDetector:
    name, device = "fake", "CPU"

    def detect(self, jpeg: bytes) -> tuple[list[dict[str, float]], float]:
        x = random.random() * 0.8
        return [{"x1": x, "y1": 0.3, "x2": x + 0.1, "y2": 0.9, "score": 0.9}] if random.random() < 0.7 else [], 3.0


@pytest.fixture
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    e = Engine(ROOT / "nightwatch" / "demo" / "facility.json", RuleScorer(), l1_timeout=0.05, ack_timeout=0.05)
    e.set_time("02:10")
    monkeypatch.setattr(server, "ENGINE", e, raising=False)
    monkeypatch.setattr(server, "PAIRING", Pairing(tmp_path / "devices.json", pin="123456"), raising=False)
    monkeypatch.setattr(server, "TRACKERS", {})
    e.start_autosave(tmp_path / "night_state.json", debounce=0.05)
    yield e
    e.stop_autosave()


def hammer(e: Engine, seconds: float, with_detector: bool, monkeypatch: pytest.MonkeyPatch) -> list[BaseException]:
    monkeypatch.setattr(server, "DETECTOR", FakeDetector() if with_detector else None)
    errors: list[BaseException] = []
    stop = threading.Event()
    seen: dict[int, int] = {}            # alert id -> highest level seen while open
    violations: list[str] = []

    def guard(fn):  # type: ignore[no-untyped-def]
        def run() -> None:
            rnd = random.Random()
            while not stop.is_set():
                try:
                    fn(rnd)
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)
                    return
        return run

    def sensors(rnd: random.Random) -> None:
        rid = rnd.choice(RESIDENTS)
        kind = rnd.choice(["bed", "hr", "motion", "location", "door"])
        ev = {"type": kind, "resident": rid, "in_bed": rnd.random() < 0.5, "bpm": rnd.uniform(50, 120),
              "level": rnd.random(), "zone": rnd.choice(ZONES), "exit": rnd.choice(["front", "garden"]),
              "open": rnd.random() < 0.6}
        e.sensor(ev)

    def staff(rnd: random.Random) -> None:
        ids = [a.id for a in list(e.alerts)]
        if ids:
            aid = rnd.choice(ids)
            rnd.choice([lambda: e.ack(aid, "s"), lambda: e.resolve(aid, "ok"), lambda: e.request_backup(aid, "s"),
                        lambda: e.confirm_record(aid, "Calm", "Reassurance", None, "", "s")])()

    def frames(rnd: random.Random) -> None:
        server._frame({"camera": rnd.choice(["hall12", "front"]), "jpeg": "AAAA"})

    def cameras(rnd: random.Random) -> None:
        server._camera_config(rnd.choice([
            {"camera": "hall12", "line": [0.5, 0.1, 0.5, 0.9], "inside": 1},
            {"camera": "hall12", "line": None},
            {"camera": "front", "enabled": rnd.random() < 0.8},
            {"camera": "hall12", "schedule": rnd.choice(["always", "night"])}]))
        time.sleep(0.002)

    def readers(rnd: random.Random) -> None:
        rnd.choice([e.view, e.report, e.snapshot, lambda: e.resident_detail("R-07"), lambda: server._health(True)])()

    def ticker(rnd: random.Random) -> None:
        e.tick()
        time.sleep(0.005)

    def monitor(rnd: random.Random) -> None:
        with e.lock:
            alerts = [(a.id, a.level, a.resolved) for a in e.alerts]
        ids = [i for i, _, _ in alerts]
        if len(ids) != len(set(ids)):
            violations.append(f"duplicate ids {ids}")
        for aid, level, resolved in alerts:
            if not resolved and level < seen.get(aid, 0):
                violations.append(f"alert {aid} dropped from {seen[aid]} to {level}")
            seen[aid] = max(seen.get(aid, 0), level)
        time.sleep(0.001)

    workers = [sensors] * 6 + [staff] * 4 + [frames] * 4 + [cameras] * 2 + [readers] * 3 + [ticker, monitor]
    threads = [threading.Thread(target=guard(w), daemon=True) for w in workers]
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive(), "a worker is stuck (deadlock?)"
    assert not violations, violations[:5]
    return errors


def test_stress_without_detector(eng: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    errors = hammer(eng, 1.5, with_detector=False, monkeypatch=monkeypatch)
    assert not errors, errors[:3]
    assert len(eng.alerts) > 0


def test_stress_with_detector_and_scenarios(eng: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    errors = hammer(eng, 1.5, with_detector=True, monkeypatch=monkeypatch)
    assert not errors, errors[:3]
    # Scenario restarts racing each other must not deadlock or leave two scenario threads running.
    threads = [threading.Thread(target=eng.run_scenario, args=("night_bathroom",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    time.sleep(0.2)
    alive = [t for t in threading.enumerate() if t.name.startswith("scenario-") and t.is_alive()]
    assert len(alive) <= 1
    eng.run_scenario("reset")
