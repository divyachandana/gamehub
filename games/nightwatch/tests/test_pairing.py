"""Phones must pair with the laptop's PIN before they can see or answer alerts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
from pairing import MAX_TRIES, Pairing  # noqa: E402


def test_laptop_is_trusted_and_strangers_are_not(tmp_path: Path) -> None:
    p = Pairing(tmp_path / "devices.json", pin="123456")
    assert p.allowed("127.0.0.1", None)
    assert not p.allowed("192.168.1.20", None)
    assert not p.allowed("192.168.1.20", "made-up-token")


def test_correct_pin_gives_a_token_that_survives_a_restart(tmp_path: Path) -> None:
    p = Pairing(tmp_path / "devices.json", pin="123456")
    ok, token = p.pair("192.168.1.20", "123456", "Night nurse")
    assert ok and p.allowed("192.168.1.20", token)
    again = Pairing(tmp_path / "devices.json", pin="999999")
    assert again.allowed("192.168.1.20", token)
    assert token not in (tmp_path / "devices.json").read_text()   # only a hash is stored


def test_wrong_pins_lock_the_device_out(tmp_path: Path) -> None:
    p = Pairing(tmp_path / "devices.json", pin="123456")
    for _ in range(MAX_TRIES):
        assert p.pair("192.168.1.30", "000000", "x") == (False, "wrong PIN")
    ok, msg = p.pair("192.168.1.30", "123456", "x")
    assert not ok and "too many" in msg
