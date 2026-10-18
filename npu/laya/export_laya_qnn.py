"""Export Laya typed-decisions to a QNN (HTP) .pte for Snapdragon X Elite (SC8380XP).

Needs the ExecuTorch source tree on PYTHONPATH with the ARM64 PyQnnManagerAdaptor built (see README.md).

  python export_laya_qnn.py --model_dir C:\\dev\\laya_src\\typed-decisions --out_dir models\\laya_typed_decisions_sc8380xp_s512
"""
import argparse
import collections
import os
import shutil
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import laya_model as lm  # noqa: E402
from samples import EXTRA, QUESTIONS, STATE  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model_dir", required=True, help="local snapshot of convaiinnovations/laya/typed-decisions")
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--soc", default="SC8380XP")
    ap.add_argument("--seq_len", type=int, default=lm.SEQ_LEN)
    ap.add_argument("--precision", choices=["fp16", "16a8w"], default="fp16")
    args = ap.parse_args()

    from executorch.backends.qualcomm.utils.utils import (
        generate_htp_compiler_spec,
        generate_qnn_executorch_compiler_spec,
        get_soc_to_chipset_map,
        to_edge_transform_and_lower_to_qnn,
    )
    from executorch.extension.export_util.utils import save_pte_program

    tok = lm.load_tokenizer(args.model_dir)
    cfg = lm.load_cfg(args.model_dir)
    model = lm.load_laya(args.model_dir)
    sample = tuple(torch.from_numpy(x) for x in
                   lm.encode_question(tok, cfg, STATE, QUESTIONS["department"], args.seq_len)[0])

    if args.precision == "16a8w":
        from executorch.backends.qualcomm.quantizer.quantizer import QnnQuantizer, QuantDtype
        from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e
        quantizer = QnnQuantizer(soc_model=get_soc_to_chipset_map()[args.soc])
        quantizer.set_default_quant_config(QuantDtype.use_16a8w)
        gm = prepare_pt2e(torch.export.export(model, sample, strict=False).module(), quantizer)
        with torch.no_grad():
            for state, qs in [(STATE, QUESTIONS)] + EXTRA:
                for qd in qs.values():
                    gm(*[torch.from_numpy(x) for x in lm.encode_question(tok, cfg, state, qd, args.seq_len)[0]])
        model = convert_pt2e(gm)

    backend_options = generate_htp_compiler_spec(use_fp16=args.precision == "fp16")
    compile_spec = generate_qnn_executorch_compiler_spec(
        soc_model=get_soc_to_chipset_map()[args.soc], backend_options=backend_options)
    with torch.no_grad():
        edge = to_edge_transform_and_lower_to_qnn(model, sample, compile_spec)

    # Report what stayed on CPU (anything not inside the QNN delegate).
    gm = edge.exported_program().graph_module
    cpu_ops = collections.Counter(str(n.target) for n in gm.graph.nodes
                                  if n.op == "call_function" and "executorch_call_delegate" not in str(n.target)
                                  and "getitem" not in str(n.target))
    n_deleg = sum(1 for n in gm.graph.nodes if "executorch_call_delegate" in str(n.target))
    print("QNN delegates: %d   ops left on CPU: %s" % (n_deleg, dict(cpu_ops) or "none"))

    prog = edge.to_executorch()
    os.makedirs(args.out_dir, exist_ok=True)
    name = "laya_typed_decisions_%s_s%d_%s" % (args.soc.lower(), args.seq_len, args.precision)
    save_pte_program(prog, name, args.out_dir)
    shutil.copytree(os.path.join(args.model_dir, "tokenizer"), os.path.join(args.out_dir, "tokenizer"), dirs_exist_ok=True)
    shutil.copy(os.path.join(args.model_dir, "rl_agent_config.json"), args.out_dir)
    print("wrote", os.path.join(args.out_dir, name + ".pte"))


if __name__ == "__main__":
    main()
