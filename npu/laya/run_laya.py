"""Run the Laya QNN .pte on the Snapdragon X NPU: state + typed questions -> calibrated answers.

All questions for one state go through a single qnn_executor_runner call (one model load, N inferences).

  python run_laya.py                                   # model-card example
  python run_laya.py --state "The app crashes on login" [--questions my_questions.json]
  python run_laya.py --interactive                     # type a state, get answers, repeat
  python run_laya.py --check                           # also run PyTorch on CPU and compare
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import laya_model as lm  # noqa: E402
from samples import QUESTIONS, STATE  # noqa: E402

DEFAULT_MODEL_DIR = os.path.join(HERE, "models", "laya_typed_decisions_sc8380xp_s512")
RUNNER = os.path.join(HERE, "bin", "qnn_executor_runner.exe")


def find_pte(model_dir):
    ptes = [f for f in os.listdir(model_dir) if f.endswith(".pte")]
    if not ptes:
        sys.exit("no .pte in %s" % model_dir)
    return os.path.join(model_dir, sorted(ptes)[0])


def run_npu(pte, batch):
    """batch: list of input lists (5 numpy arrays each) -> list of (logits[MAX_OPTS], act_logits[2]), ms/inference."""
    with tempfile.TemporaryDirectory(prefix="laya_") as td:
        lines = []
        for i, inputs in enumerate(batch):
            paths = []
            for j, arr in enumerate(inputs):
                p = os.path.join(td, "in_%d_%d.raw" % (i, j))
                np.ascontiguousarray(arr).tofile(p)
                paths.append(p)
            lines.append(" ".join(paths))
        with open(os.path.join(td, "input_list.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        out_dir = os.path.join(td, "out")
        os.makedirs(out_dir)
        cmd = [RUNNER, "--model_path", pte, "--input_list_path", os.path.join(td, "input_list.txt"),
               "--output_folder_path", out_dir, "--performance_output_path", os.path.join(td, "perf.txt")]
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        log = proc.stdout + proc.stderr
        results = []
        for i in range(len(batch)):
            lo, act = (os.path.join(out_dir, "output_%d_%d.raw" % (i, k)) for k in (0, 1))
            if not (os.path.exists(lo) and os.path.exists(act)):
                sys.exit("runner produced no output (exit %s). Log tail:\n%s" % (proc.returncode, log[-3000:]))
            results.append((np.fromfile(lo, dtype=np.float32), np.fromfile(act, dtype=np.float32)))
        m = re.search(r"Total (\d+) inference took ([\d.]+) ms", log)
        return results, (float(m.group(2)) / int(m.group(1)) if m else float("nan"))


def answer(tok, cfg, pte, state, questions, check_model=None):
    enc = {qid: lm.encode_question(tok, cfg, state, qd) for qid, qd in questions.items()}
    t0 = time.time()
    outs, ms = run_npu(pte, [e[0] for e in enc.values()])
    wall = (time.time() - t0) * 1000
    answers = {}
    for (qid, (inputs, q, k, n)), (lo, act) in zip(enc.items(), outs):
        answers[qid] = lm.postprocess(cfg, q, k, lo, act)
        if check_model is not None:
            import torch
            with torch.no_grad():
                rlo, ract = check_model(*[torch.from_numpy(x) for x in inputs])
            answers[qid]["cpu_reference"] = lm.postprocess(cfg, q, k, rlo[0].numpy(), ract[0].numpy())
    n_tok = sum(e[3] for e in enc.values())
    return {"model": "laya-typed-decisions (QNN HTP fp16, SC8380XP)", "answers": answers,
            "usage": {"input_tokens": n_tok, "output_tokens": 0},
            "timing_ms": {"npu_per_question": round(ms, 2), "total_wall_incl_model_load": round(wall, 1)}}


def parse_state(s):
    if s.startswith("@"):
        with open(s[1:], encoding="utf-8") as f:
            s = f.read()
    try:
        return json.loads(s)
    except ValueError:
        return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model_dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument("--state", help="text, JSON string, or @file")
    ap.add_argument("--questions", help="JSON file of {id: {type, instructions, criteria}} (default: model-card set)")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--check", action="store_true", help="compare against PyTorch CPU (needs model weights dir)")
    ap.add_argument("--weights_dir", default=r"C:\dev\laya_src\typed-decisions", help="for --check")
    args = ap.parse_args()

    model_dir = os.path.abspath(args.model_dir)
    pte = find_pte(model_dir)
    tok = lm.load_tokenizer(model_dir)
    cfg = lm.load_cfg(model_dir)
    questions = QUESTIONS
    if args.questions:
        with open(args.questions, encoding="utf-8") as f:
            questions = json.load(f)
        if isinstance(questions.get("questions"), dict):  # a scenario file: use its question set
            questions = questions["questions"]
    ref = lm.load_laya(args.weights_dir) if args.check else None

    if args.interactive:
        print("Laya on NPU. Questions: %s. Type a message (or 'exit')." % ", ".join(questions))
        while True:
            try:
                s = input("\nstate> ").strip()
            except EOFError:
                break
            if s.lower() in ("exit", "quit"):
                break
            if s:
                r = answer(tok, cfg, pte, parse_state(s), questions, ref)
                for qid, a in r["answers"].items():
                    val = a.get("choice", a.get("score", a.get("noul")))
                    print("  %-18s %s" % (qid, val))
                print("  (%.1f ms/question on NPU)" % r["timing_ms"]["npu_per_question"])
        return

    state = parse_state(args.state) if args.state else STATE
    print(json.dumps(answer(tok, cfg, pte, state, questions, ref), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
