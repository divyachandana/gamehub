"""Resident Laya on the NPU: bin\\qnn_static_host.exe loads the .pte once, then answers in ~0.3 s per question.

    from laya_host import LayaHost
    with LayaHost() as laya:
        print(laya.ask(state, {"type": "noul", "instructions": "Is the user angry?"}))
        print(laya.ask_all(state, questions))     # {id: answer}

Needs the QNN env (setup_env.ps1) or pass qnn_sdk_root, which sets it for the child process.
"""
import os
import subprocess
import threading

import numpy as np

import laya_model as lm

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_DIR = os.path.join(HERE, "models", "laya_typed_decisions_sc8380xp_s512")
HOST_EXE = os.path.join(HERE, "bin", "qnn_static_host.exe")
QNN_SDK = r"C:\Qualcomm\AIStack\QAIRT\2.46.0.260424"


class LayaHost:
    def __init__(self, model_dir=DEFAULT_MODEL_DIR, qnn_sdk_root=QNN_SDK):
        self.tok = lm.load_tokenizer(model_dir)
        self.cfg = lm.load_cfg(model_dir)
        pte = sorted(f for f in os.listdir(model_dir) if f.endswith(".pte"))[0]
        env = dict(os.environ)
        env["QNN_SDK_ROOT"] = qnn_sdk_root
        env["PATH"] = os.path.join(qnn_sdk_root, "lib", "aarch64-windows-msvc") + os.pathsep + env.get("PATH", "")
        env["ADSP_LIBRARY_PATH"] = os.path.join(qnn_sdk_root, "lib", "hexagon-v73", "unsigned")
        self.proc = subprocess.Popen([HOST_EXE, os.path.join(model_dir, pte)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
        self.lock = threading.Lock()
        line = self._read()
        if not line.startswith("@@READY"):
            raise RuntimeError("laya host failed to start: " + line)

    def _read(self):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("laya host exited (code %s)" % self.proc.poll())
            if line.startswith("@@"):
                return line.strip()

    def raw(self, inputs):
        """inputs: the 5 numpy arrays from laya_model.encode_question -> (logits[16], act[2], npu_ms)."""
        msg = " ".join(" ".join(str(v) for v in np.asarray(a).ravel().tolist()) for a in inputs)
        with self.lock:
            self.proc.stdin.write(msg + "\n")
            self.proc.stdin.flush()
            line = self._read()
        if not line.startswith("@@OK"):
            raise RuntimeError(line)
        vals = [float(x) for x in line.split()[1:]]
        return np.array(vals[1:1 + lm.MAX_OPTS]), np.array(vals[1 + lm.MAX_OPTS:3 + lm.MAX_OPTS]), vals[0]

    def ask(self, state, qdef):
        """One typed question -> Laya answer dict (+ "npu_ms")."""
        inputs, q, k, _ = lm.encode_question(self.tok, self.cfg, state, qdef)
        lo, act, ms = self.raw(inputs)
        a = lm.postprocess(self.cfg, q, k, lo, act)
        a["npu_ms"] = round(ms, 1)
        return a

    def ask_all(self, state, questions):
        return {qid: self.ask(state, qd) for qid, qd in questions.items()}

    def close(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write("quit\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
