r"""Gemma on the Snapdragon NPU: runs ExecuTorch's ``qnn_llm_host.exe`` over its named pipe.

The host loads the QNN-compiled ``.pte`` once and then serves newline-delimited JSON over
``\\.\pipe\<name>``. Every junction is a one-shot request (``"reset": true``), so the
KV cache never fills up during a race. Pipe I/O is blocking; callers run it in a thread.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from pydantic import BaseModel, ConfigDict, Field

from san_francisco.errors import DeciderError

logger = logging.getLogger(__name__)

HOST_START_TIMEOUT_S = 120.0
PIPE_POLL_S = 0.3
SHUTDOWN_TIMEOUT_S = 10.0
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

DeltaCallback = Callable[[str], None]


class QnnHostError(DeciderError):
    """The NPU host could not be started or stopped answering."""


class QnnModelSettings(BaseModel):
    """One QNN-compiled model in the bundle (an entry of ``qnn.models``)."""

    model_config = ConfigDict(frozen=True)

    model_dir: str = Field(description="Model folder, relative to bundle_dir")
    pte: str = Field(description="QNN-compiled .pte inside model_dir")
    tokenizer: str = "tokenizer.json"
    decoder_model_version: str = Field(description="Chat template, e.g. gemma4 or qwen3")
    max_seq_len: int = Field(default=3800, gt=0, description="Under the compiled context length")
    prompt_suffix: str = Field(default="", description="Appended to every prompt, e.g. /no_think")


class QnnHostSettings(BaseModel):
    """Where the ExecuTorch QNN bundle lives and its models (``qnn:`` in ``racers.yaml``)."""

    model_config = ConfigDict(frozen=True)

    bundle_dir: Path = Field(description="Folder with host/qnn_llm_host.exe and models/")
    qnn_sdk: Path = Field(description="QAIRT SDK root")
    hexagon_arch: str = Field(default="v73", description="HTP version (SC8380XP = v73)")
    models: dict[str, QnnModelSettings]

    def command(self, model_key: str) -> list[str]:
        """Command line that starts a host serving ``model_key`` on its own pipe."""
        model = self.models[model_key]
        model_dir = self.bundle_dir / model.model_dir
        return [
            str(self.bundle_dir / "host" / "qnn_llm_host.exe"),
            "--model_path", str(model_dir / model.pte),
            "--tokenizer_path", str(model_dir / model.tokenizer),
            "--decoder_model_version", model.decoder_model_version,
            "--pipe_name", pipe_name(model_key),
            "--seq_len", str(model.max_seq_len),
            # Otherwise the host writes inference_speed.txt into the working directory.
            "--performance_output_path", str(LOG_DIR / f"qnn_{model_key}_speed.txt"),
        ]  # fmt: skip

    def environment(self) -> dict[str, str]:
        """Process environment with the QNN runtime libraries and HTP skel on the search paths."""
        env = dict(os.environ)
        env["QNN_SDK_ROOT"] = str(self.qnn_sdk)
        # The backend DLL ships next to the host exe; QnnHtp.dll and friends live in the SDK.
        lib_dirs = [self.bundle_dir / "host", self.qnn_sdk / "lib" / "aarch64-windows-msvc"]
        env["PATH"] = os.pathsep.join([*map(str, lib_dirs), env.get("PATH", "")])
        # Snapdragon X needs the skel .so and its .cat signature from this one folder.
        env["ADSP_LIBRARY_PATH"] = str(
            self.qnn_sdk / "lib" / f"hexagon-{self.hexagon_arch}" / "unsigned"
        )
        return env


@dataclass(frozen=True)
class HostReply:
    """Final answer for one request, with the host's own token counts."""

    text: str
    prompt_tokens: int | None
    generated_tokens: int | None


class QnnHost:
    """One ``qnn_llm_host`` process (or an already running one) serving one model.

    The host answers one request at a time, so requests are serialized with a lock.
    """

    def __init__(self, settings: QnnHostSettings, model_key: str) -> None:
        """Bind to ``model_key``'s pipe; nothing is started until :meth:`start`."""
        self._settings = settings
        self._model_key = model_key
        self._max_seq_len = settings.models[model_key].max_seq_len
        self._pipe_name = pipe_name(model_key)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._pipe: IO[bytes] | None = None

    def start(self) -> None:
        """Connect to the pipe, launching the host first if nobody is serving it.

        Raises:
            QnnHostError: If the host exits or does not open its pipe in time.
        """
        with self._lock:
            if self._pipe is None:
                self._connect_or_launch()

    def generate(
        self, system_prompt: str, prompt: str, seq_len: int, on_delta: DeltaCallback
    ) -> HostReply:
        """Run one fresh-context generation, calling ``on_delta`` for each streamed piece.

        Args:
            system_prompt: System instructions (the host applies the chat template).
            prompt: User message.
            seq_len: Position ceiling: prompt tokens plus the reply budget.
            on_delta: Called from this thread for every streamed text piece.

        Raises:
            QnnHostError: If the pipe breaks or the host reports a failure.
        """
        request = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "seq_len": min(seq_len, self._max_seq_len),
            "temperature": 0.0,
            "reset": True,
            "stream": True,
            "include_metrics": True,
        }
        with self._lock:
            if self._pipe is None:
                self._connect_or_launch()
            try:
                self._send(request)
                while "delta" in (line := self._read_line()):
                    on_delta(str(line["delta"]))
            except (OSError, ValueError) as exc:
                self._drop_pipe()  # host died: relaunch on the next request
                raise QnnHostError(f"NPU host pipe failed: {exc}") from exc
        if not line.get("ok"):
            raise QnnHostError(f"NPU host error: {line.get('error')}")
        metrics = line.get("metrics") or {}
        return HostReply(
            text=str(line.get("text", "")),
            prompt_tokens=metrics.get("prompt_tokens"),
            generated_tokens=metrics.get("generated_tokens"),
        )

    def close(self) -> None:
        """Stop the host if we launched it; just disconnect from one started by hand."""
        with self._lock:
            if self._pipe is not None and self._proc is not None:
                try:
                    self._send({"cmd": "shutdown"})
                    self._read_line()
                except (OSError, ValueError):
                    logger.debug("NPU host already gone", exc_info=True)
            self._drop_pipe()
            if self._proc is not None:
                try:
                    self._proc.wait(timeout=SHUTDOWN_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                self._proc = None

    # ----------------------------------------------------------------------- internals

    def _connect_or_launch(self) -> None:
        if self._try_connect():
            logger.info("Using running NPU host on pipe %s", self._pipe_name)
            return
        if self._proc is None or self._proc.poll() is not None:
            self._launch()
        deadline = time.monotonic() + HOST_START_TIMEOUT_S
        while not self._try_connect():
            assert self._proc is not None
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self._proc = None
                raise QnnHostError(f"NPU host exited with code {code}; see {self._log_path()}")
            if time.monotonic() > deadline:
                raise QnnHostError("NPU host did not open its pipe in time")
            time.sleep(PIPE_POLL_S)
        logger.info("NPU host ready on pipe %s", self._pipe_name)

    def _launch(self) -> None:
        command = self._settings.command(self._model_key)
        if not Path(command[0]).is_file():
            raise QnnHostError(f"NPU host not found: {command[0]}")
        LOG_DIR.mkdir(exist_ok=True)
        log = self._log_path().open("ab")
        logger.info("Launching NPU host: %s", " ".join(command))
        self._proc = subprocess.Popen(
            command, env=self._settings.environment(), stdout=log, stderr=subprocess.STDOUT
        )
        log.close()  # the child keeps its own handle

    def _log_path(self) -> Path:
        return LOG_DIR / f"qnn_{self._model_key}.log"

    def _try_connect(self) -> bool:
        try:
            # Unbuffered: a buffered reader would block waiting for more than one line.
            self._pipe = open(rf"\\.\pipe\{self._pipe_name}", "r+b", buffering=0)  # noqa: SIM115
        except OSError:
            return False
        return True

    def _send(self, payload: dict[str, Any]) -> None:
        assert self._pipe is not None
        self._pipe.write((json.dumps(payload) + "\n").encode("utf-8"))

    def _read_line(self) -> dict[str, Any]:
        assert self._pipe is not None
        buffer = bytearray()
        while not buffer.endswith(b"\n"):
            byte = self._pipe.read(1)
            if not byte:
                raise OSError("NPU host closed the pipe")
            buffer += byte
        data = json.loads(buffer.decode("utf-8", "replace"))
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected host reply: {data!r}")
        return data

    def _drop_pipe(self) -> None:
        if self._pipe is not None:
            with contextlib.suppress(OSError):
                self._pipe.close()
            self._pipe = None


_hosts: dict[str, QnnHost] = {}


def pipe_name(model_key: str) -> str:
    """Name of the pipe that serves ``model_key`` (one host process per model)."""
    return f"rally_{model_key}"


def get_host(settings: QnnHostSettings, model_key: str) -> QnnHost:
    """Shared host for ``model_key``: one model load serves every race."""
    if model_key not in _hosts:
        _hosts[model_key] = QnnHost(settings, model_key)
    return _hosts[model_key]


def close_all() -> None:
    """Stop every host this process launched (server shutdown)."""
    for host in _hosts.values():
        host.close()
    _hosts.clear()
