"""Export the normal-pattern model to ExecuTorch .pte files.

    python nightwatch/model/export.py --backend xnnpack   # CPU: Mac, Linux, Windows
    python nightwatch/model/export.py --backend qnn       # Hexagon NPU, Snapdragon X Elite (SC8380XP)

QNN ahead-of-time compile needs an x86_64 host with the Qualcomm AI Engine Direct (QNN) SDK and
ExecuTorch's Qualcomm backend built (QNN_SDK_ROOT set); it can't run on a Mac or natively on
Windows on ARM. Copy the .pte to the laptop; the QNN runtime libraries there must match the SDK.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch

from train_anomaly import ROOT, NormalPattern

EXAMPLE = (torch.zeros(1, 8),)


def load() -> NormalPattern:
    model = NormalPattern()
    model.load_state_dict(torch.load(ROOT / "models" / "normal_pattern.pt", weights_only=True))
    return model.eval()


def export_xnnpack(model: NormalPattern) -> Path:
    from executorch.backends.xnnpack.partition.xnnpack_partitioner import XnnpackPartitioner
    from executorch.exir import to_edge_transform_and_lower

    program = torch.export.export(model, EXAMPLE)
    return _write(to_edge_transform_and_lower(program, partitioner=[XnnpackPartitioner()]).to_executorch(),
                  "normal_pattern_xnnpack.pte")


def export_qnn(model: NormalPattern) -> Path:
    from executorch.backends.qualcomm.serialization.qc_schema import QcomChipset
    from executorch.backends.qualcomm.utils.utils import (
        generate_htp_compiler_spec,
        generate_qnn_executorch_compiler_spec,
        to_edge_transform_and_lower_to_qnn,
    )

    # fp16 on the HTP needs no calibration data. If this SoC rejects fp16, quantize with
    # QnnQuantizer and a few hundred normal samples (see ExecuTorch Qualcomm docs).
    specs = generate_qnn_executorch_compiler_spec(
        soc_model=QcomChipset.SC8380XP, backend_options=generate_htp_compiler_spec(use_fp16=True))
    return _write(to_edge_transform_and_lower_to_qnn(model, EXAMPLE, specs).to_executorch(), "normal_pattern_qnn.pte")


def _write(pte: object, name: str) -> Path:
    out = ROOT / "models" / name
    out.write_bytes(pte.buffer)  # type: ignore[attr-defined]
    print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KB)")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", choices=["xnnpack", "qnn"], default="xnnpack")
    args = p.parse_args()
    model = load()
    path = export_xnnpack(model) if args.backend == "xnnpack" else export_qnn(model)
    if args.backend == "xnnpack":
        from executorch.runtime import Runtime

        x = torch.rand(1, 8)
        (got,) = Runtime.get().load_program(str(path)).load_method("forward").execute([x])
        print(f"check: max |pte - eager| = {float((got - model(x)).abs().max()):.2e}")


if __name__ == "__main__":
    main()
