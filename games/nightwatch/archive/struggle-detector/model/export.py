"""Export the trained frustration model to ExecuTorch .pte files.

    python model/export.py --backend xnnpack     # CPU. Works on Mac, Linux, Windows.
    python model/export.py --backend qnn         # Hexagon NPU (Snapdragon X Elite, SC8380XP).

QNN ahead-of-time compile needs an x86_64 host with the Qualcomm AI Engine Direct (QNN) SDK
and ExecuTorch's Qualcomm backend built (QNN_SDK_ROOT set). It cannot run on a Mac or natively
on Windows on ARM. The model is tiny, so the compile takes seconds once the SDK is set up.
Copy the resulting .pte to the laptop and run:
    python server/app.py --model models/frustration_qnn.pte --device NPU
The .pte is tied to the SoC and the QNN SDK version, so the laptop's QNN runtime libraries
must match the SDK used here.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch

from train import ROOT, FrustrationNet

EXAMPLE = (torch.zeros(1, 10),)


def load() -> FrustrationNet:
    model = FrustrationNet()
    model.load_state_dict(torch.load(ROOT / "models" / "frustration.pt", weights_only=True))
    return model.eval()


def export_xnnpack(model: FrustrationNet) -> Path:
    from executorch.backends.xnnpack.partition.xnnpack_partitioner import XnnpackPartitioner
    from executorch.exir import to_edge_transform_and_lower

    program = torch.export.export(model, EXAMPLE)
    pte = to_edge_transform_and_lower(program, partitioner=[XnnpackPartitioner()]).to_executorch()
    return _write(pte, "frustration_xnnpack.pte")


def export_qnn(model: FrustrationNet) -> Path:
    from executorch.backends.qualcomm.serialization.qc_schema import QcomChipset
    from executorch.backends.qualcomm.utils.utils import (
        generate_htp_compiler_spec,
        generate_qnn_executorch_compiler_spec,
        to_edge_transform_and_lower_to_qnn,
    )

    # fp16 on the HTP keeps this simple (no calibration data needed). If the compile
    # rejects fp16 on this SoC, quantize with QnnQuantizer (see ExecuTorch Qualcomm docs).
    specs = generate_qnn_executorch_compiler_spec(
        soc_model=QcomChipset.SC8380XP,
        backend_options=generate_htp_compiler_spec(use_fp16=True),
    )
    pte = to_edge_transform_and_lower_to_qnn(model, EXAMPLE, specs).to_executorch()
    return _write(pte, "frustration_qnn.pte")


def _write(pte: object, name: str) -> Path:
    out = ROOT / "models" / name
    out.write_bytes(pte.buffer)  # type: ignore[attr-defined]
    print(f"wrote {out} ({out.stat().st_size / 1024:.1f} KB)")
    return out


def check(path: Path, model: FrustrationNet) -> None:
    """Run the .pte once and compare with eager PyTorch."""
    from executorch.runtime import Runtime

    x = torch.rand(1, 10)
    (got,) = Runtime.get().load_program(str(path)).load_method("forward").execute([x])
    want = model(x)
    print(f"check: pte {float(got.flatten()[0]):.5f} vs eager {float(want.flatten()[0]):.5f}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", choices=["xnnpack", "qnn"], default="xnnpack")
    args = p.parse_args()
    model = load()
    path = export_xnnpack(model) if args.backend == "xnnpack" else export_qnn(model)
    if args.backend == "xnnpack":
        check(path, model)


if __name__ == "__main__":
    main()
