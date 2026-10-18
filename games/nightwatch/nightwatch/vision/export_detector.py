"""Export a person detector (torchvision SSDlite320-MobileNetV3, COCO weights) to ExecuTorch.

    python nightwatch/vision/export_detector.py --backend xnnpack    # CPU: Mac, Linux, Windows
    python nightwatch/vision/export_detector.py --backend qnn        # Hexagon NPU (x86_64 host + QNN SDK)
    python nightwatch/vision/export_detector.py --backend xnnpack --int8 --calib data/calib   # 8-bit CPU model
    python nightwatch/vision/export_detector.py --backend qnn --int8 --calib data/calib       # 8-bit NPU model

--int8 quantizes weights and activations (post-training, PT2E) using calibration images: a folder of
JPEGs from the actual camera (data/calib, git-ignored; capture a few dozen during setup, with consent,
then delete them). Without --calib it falls back to built-in sample photos, which is fine for speed
tests but not for best accuracy.

Only the neural network goes into the .pte (backbone + detection head: the heavy part). The
anchor boxes are fixed for a 320x320 input, so they are computed once here and saved next to
the model; decoding and non-maximum suppression run in plain PyTorch at runtime (detector.py).
Weights: torchvision's pretrained COCO checkpoint (BSD-licensed), downloaded on first run.
Writes models/person_ssdlite_<backend>.pte and models/person_ssdlite_anchors.pt.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
SIZE = 320


class DetectorCore(nn.Module):
    """Image (1,3,320,320), already normalized -> (box deltas (1,A,4), class logits (1,A,91))."""

    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.backbone = model.backbone
        self.head = model.head

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        features = list(self.backbone(x).values())
        out = self.head(features)
        return out["bbox_regression"], out["cls_logits"]


def load() -> tuple[DetectorCore, torch.Tensor]:
    from torchvision.models.detection import SSDLite320_MobileNet_V3_Large_Weights, ssdlite320_mobilenet_v3_large
    from torchvision.models.detection.image_list import ImageList

    model = ssdlite320_mobilenet_v3_large(weights=SSDLite320_MobileNet_V3_Large_Weights.COCO_V1).eval()
    core = DetectorCore(model).eval()
    with torch.no_grad():
        x = torch.zeros(1, 3, SIZE, SIZE)
        features = list(model.backbone(x).values())
        anchors = model.anchor_generator(ImageList(x, [(SIZE, SIZE)]), features)[0]
    return core, anchors


def calibration_frames(folder: Path | None) -> list[torch.Tensor]:
    """Normalized 320x320 frames for quantization calibration, with flips and darkened copies (night)."""
    from PIL import Image, ImageEnhance

    paths = sorted(folder.glob("*.jp*g")) if folder else []
    if not paths:
        print("WARNING: no calibration images (--calib): using random frames. Fine for speed tests, "
              "not for accuracy. Calibrate on real camera frames before the demo.")
    frames = []
    for path in paths:
        img = Image.open(path).convert("RGB").resize((SIZE, SIZE))
        for variant in (img, img.transpose(Image.FLIP_LEFT_RIGHT), ImageEnhance.Brightness(img).enhance(0.4)):
            x = torch.frombuffer(bytearray(variant.tobytes()), dtype=torch.uint8).view(SIZE, SIZE, 3)
            frames.append(x.permute(2, 0, 1).float().div(255).sub(0.5).div(0.5).unsqueeze(0).contiguous())
    if not frames:
        frames = [torch.rand(1, 3, SIZE, SIZE) * 2 - 1 for _ in range(16)]
    return frames


def quantize(core: DetectorCore, backend: str, frames: list[torch.Tensor]) -> torch.nn.Module:
    from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e

    if backend == "xnnpack":
        from executorch.backends.xnnpack.quantizer.xnnpack_quantizer import (
            XNNPACKQuantizer,
            get_symmetric_quantization_config,
        )

        quantizer = XNNPACKQuantizer().set_global(get_symmetric_quantization_config(is_per_channel=True))
    else:
        from executorch.backends.qualcomm.quantizer.quantizer import QnnQuantizer

        quantizer = QnnQuantizer()
    graph = torch.export.export(core, (frames[0],)).module()
    prepared = prepare_pt2e(graph, quantizer)
    with torch.no_grad():
        for x in frames:
            prepared(x)
    print(f"calibrated on {len(frames)} frames")
    return convert_pt2e(prepared)


def export(core: torch.nn.Module, backend: str, suffix: str = "") -> Path:
    example = (torch.rand(1, 3, SIZE, SIZE) * 2 - 1,)
    if backend == "xnnpack":
        from executorch.backends.xnnpack.partition.xnnpack_partitioner import XnnpackPartitioner
        from executorch.exir import to_edge_transform_and_lower

        program = torch.export.export(core, example)
        pte = to_edge_transform_and_lower(program, partitioner=[XnnpackPartitioner()]).to_executorch()
    else:
        from executorch.backends.qualcomm.serialization.qc_schema import QcomChipset
        from executorch.backends.qualcomm.utils.utils import (
            generate_htp_compiler_spec,
            generate_qnn_executorch_compiler_spec,
            to_edge_transform_and_lower_to_qnn,
        )

        # fp16 needs no calibration; with --int8 the module is already quantized (QnnQuantizer).
        specs = generate_qnn_executorch_compiler_spec(
            soc_model=QcomChipset.SC8380XP, backend_options=generate_htp_compiler_spec(use_fp16=not suffix))
        pte = to_edge_transform_and_lower_to_qnn(core, example, specs).to_executorch()
    out = ROOT / "models" / f"person_ssdlite_{backend}{suffix}.pte"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(pte.buffer)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backend", choices=["xnnpack", "qnn"], default="xnnpack")
    p.add_argument("--int8", action="store_true", help="post-training 8-bit quantization")
    p.add_argument("--calib", type=Path, help="folder of camera JPEGs for calibration (git-ignored data/)")
    args = p.parse_args()
    core, anchors = load()
    torch.save(anchors, ROOT / "models" / "person_ssdlite_anchors.pt")
    print(f"anchors: {tuple(anchors.shape)}")
    model = quantize(core, args.backend, calibration_frames(args.calib)) if args.int8 else core
    path = export(model, args.backend, "_int8" if args.int8 else "")
    if args.backend == "xnnpack":
        from executorch.runtime import Runtime

        x = torch.rand(1, 3, SIZE, SIZE) * 2 - 1
        got = Runtime.get().load_program(str(path)).load_method("forward").execute([x])
        with torch.no_grad():
            want = core(x)
        print("check vs float eager: max |diff| =", max(float((g - w).abs().max()) for g, w in zip(got, want)),
              "(small for float, larger but expected for --int8)")


if __name__ == "__main__":
    main()
