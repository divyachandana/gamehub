"""Run a test scenario (questions + cases with expected answers) through Laya on the NPU and score it.

  python run_scenario.py scenarios\\food_delivery.json [--check] [--probs]

Pass rules: choice = same option; score = within 0.75 of expected level; noul = same side of 0.5.
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import laya_model as lm  # noqa: E402
import run_laya as rl  # noqa: E402


def value(a):
    return a["choice"] if a["type"] == "choice" else a["score"] if a["type"] == "score" else a["noul"]


def passed(a, exp):
    if a["type"] == "choice":
        return a["choice"] == exp
    if a["type"] == "score":
        return abs(a["score"] - exp) <= 0.75
    return (a["noul"] > 0.5) == bool(exp)


def show(a):
    v = value(a)
    if a["type"] == "choice":
        return "%s (%.0f%%)" % (v, 100 * a["probabilities"][v])
    if a["type"] == "score":
        return "%.2f" % v
    return "%s (%.2f)" % ("yes" if v > 0.5 else "no", v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--model_dir", default=rl.DEFAULT_MODEL_DIR)
    ap.add_argument("--check", action="store_true", help="also score PyTorch CPU on the same cases")
    ap.add_argument("--weights_dir", default=r"C:\dev\laya_src\typed-decisions")
    ap.add_argument("--probs", action="store_true", help="print the full probability of every option")
    args = ap.parse_args()

    with open(args.scenario, encoding="utf-8") as f:
        sc = json.load(f)
    model_dir = os.path.abspath(args.model_dir)
    tok, cfg = lm.load_tokenizer(model_dir), lm.load_cfg(model_dir)
    questions, cases = sc["questions"], sc["cases"]

    # Every (case, question) pair in one runner call: one model load, N inferences.
    enc = [(ci, qid, lm.encode_question(tok, cfg, c["state"], questions[qid]))
           for ci, c in enumerate(cases) for qid in questions]
    print("Running %s: %d questions on the NPU, please wait ~%d s..."
          % (os.path.basename(args.scenario), len(enc), 3 + len(enc) * 0.4), flush=True)
    t0 = time.time()
    outs, ms = rl.run_npu(rl.find_pte(model_dir), [e[2][0] for e in enc])
    wall = time.time() - t0
    ref = lm.load_laya(args.weights_dir) if args.check else None

    results = [dict() for _ in cases]
    cpu = [dict() for _ in cases]
    for (ci, qid, (inputs, q, k, n)), (lo, act) in zip(enc, outs):
        results[ci][qid] = lm.postprocess(cfg, q, k, lo, act)
        if ref is not None:
            import torch
            with torch.no_grad():
                rlo, ract = ref(*[torch.from_numpy(x) for x in inputs])
            cpu[ci][qid] = lm.postprocess(cfg, q, k, rlo[0].numpy(), ract[0].numpy())

    print("\n== %s ==\n%s\n" % (sc["name"], sc.get("description", "")))
    score = {qid: 0 for qid in questions}
    for ci, c in enumerate(cases):
        st = c["state"] if isinstance(c["state"], str) else json.dumps(c["state"])
        print("[%d] %s" % (ci + 1, st if len(st) <= 110 else st[:107] + "..."))
        for qid in questions:
            a, exp = results[ci][qid], c["expected"][qid]
            ok = passed(a, exp)
            score[qid] += ok
            exp_s = ("yes" if exp else "no") if questions[qid]["type"] == "noul" else str(exp)
            line = "    %-4s %-13s %-26s expected %s" % ("PASS" if ok else "MISS", qid, show(a), exp_s)
            if ref is not None:
                line += "   | cpu: %s" % show(cpu[ci][qid])
            print(line)
            if args.probs and "probabilities" in a:
                print("           " + "  ".join("%s=%.2f" % kv for kv in a["probabilities"].items()))
    total = sum(score.values())
    n = len(cases)
    print("\nScore: %d/%d   (%s)" % (total, n * len(questions),
                                    ", ".join("%s %d/%d" % (q, s, n) for q, s in score.items())))
    print("NPU: %.0f ms per question, %d questions, %.1f s total incl. model load" % (ms, len(enc), wall))


if __name__ == "__main__":
    main()
