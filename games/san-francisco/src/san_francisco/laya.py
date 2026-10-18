"""Laya on the NPU: the rogue racer's "System 1" instinct for when to cheat.

Laya (a ModernBERT decision model, not a chat model) answers typed questions with
calibrated probabilities in ~0.3 s on the NPU. Its host code needs PyTorch, so it runs in
a separate Python (``scripts/laya_bridge.py``) and is spoken to over stdin/stdout.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, ConfigDict, Field

from san_francisco.errors import DeciderError

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPT = ROOT / "scripts" / "laya_bridge.py"
LOG_PATH = ROOT / "logs" / "laya.log"
SHUTDOWN_TIMEOUT_S = 10.0


class LayaError(DeciderError):
    """The Laya bridge could not be started or stopped answering."""


class LayaSettings(BaseModel):
    """Where Laya lives (``laya:`` in ``racers.yaml``)."""

    model_config = ConfigDict(frozen=True)

    laya_dir: Path = Field(description="laya_x_elite folder (laya_host.py, models/, bin/)")
    python: Path = Field(description="Python with torch + transformers (Laya's tokenizer)")


class LayaBridge:
    """One Laya bridge process; questions are answered one at a time."""

    def __init__(self, settings: LayaSettings) -> None:
        """Bind to ``settings``; nothing is started until :meth:`start`."""
        self._settings = settings
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        """Launch the bridge and wait until Laya is loaded on the NPU.

        Raises:
            LayaError: If the bridge exits before reporting ready.
        """
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._launch()

    def noul(self, state: str, instructions: str) -> float:
        """Probability (0-1) that the yes/no statement ``instructions`` holds for ``state``.

        Raises:
            LayaError: If the bridge is gone or Laya reports an error.
        """
        answer = self._ask(
            {"state": state, "question": {"type": "noul", "instructions": instructions}}
        )
        return float(answer["noul"])

    def close(self) -> None:
        """Stop the bridge (and with it Laya's NPU host)."""
        with self._lock:
            if self._proc is None:
                return
            try:
                self._write({"cmd": "quit"})
                self._proc.wait(timeout=SHUTDOWN_TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()
            self._proc = None

    def _ask(self, request: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._launch()
            try:
                self._write(request)
                answer = self._read()
            except (OSError, ValueError) as exc:
                self._proc = None  # relaunch on the next question
                raise LayaError(f"Laya bridge failed: {exc}") from exc
        if "error" in answer:
            raise LayaError(f"Laya error: {answer['error']}")
        return answer

    def _launch(self) -> None:
        settings = self._settings
        for path in (settings.python, settings.laya_dir / "laya_host.py"):
            if not path.is_file():
                raise LayaError(f"Not found: {path}")
        LOG_PATH.parent.mkdir(exist_ok=True)
        log = LOG_PATH.open("ab")
        command = [str(settings.python), str(BRIDGE_SCRIPT), str(settings.laya_dir)]
        logger.info("Launching Laya bridge: %s", " ".join(command))
        self._proc = subprocess.Popen(
            command,
            cwd=settings.laya_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            bufsize=1,
        )
        log.close()  # the child keeps its own handle
        try:
            ready = self._read()
        except (OSError, ValueError) as exc:
            self._proc.kill()
            self._proc = None
            raise LayaError(f"Laya did not start ({exc}); see {LOG_PATH}") from exc
        if not ready.get("ready"):
            raise LayaError(f"Unexpected Laya greeting: {ready}")
        logger.info("Laya ready on the NPU")

    def _write(self, payload: dict[str, Any]) -> None:
        stdin = self._pipe("stdin")
        stdin.write(json.dumps(payload) + "\n")
        stdin.flush()

    def _read(self) -> dict[str, Any]:
        line = self._pipe("stdout").readline()
        if not line:
            raise OSError("Laya bridge closed its output")
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected Laya reply: {data!r}")
        return data

    def _pipe(self, name: str) -> IO[str]:
        assert self._proc is not None
        pipe: IO[str] | None = getattr(self._proc, name)
        assert pipe is not None
        return pipe


_bridge: LayaBridge | None = None


def get_bridge(settings: LayaSettings) -> LayaBridge:
    """Shared bridge: Laya loads once and serves every race."""
    global _bridge  # noqa: PLW0603 - one NPU model per process, like the QNN hosts
    if _bridge is None:
        _bridge = LayaBridge(settings)
    return _bridge


def close_all() -> None:
    """Stop the bridge if it was started (server shutdown)."""
    global _bridge  # noqa: PLW0603
    if _bridge is not None:
        _bridge.close()
        _bridge = None
