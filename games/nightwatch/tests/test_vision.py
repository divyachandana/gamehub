"""Box decoding and suppression around the ExecuTorch person detector."""
import io
import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "nightwatch"))
from vision.detector import decode, nms  # noqa: E402


def test_zero_deltas_decode_to_the_anchors() -> None:
    anchors = torch.tensor([[10.0, 20.0, 50.0, 80.0], [0.0, 0.0, 320.0, 320.0]])
    assert torch.allclose(decode(torch.zeros(2, 4), anchors), anchors)


def test_nms_drops_overlapping_lower_score_box() -> None:
    boxes = torch.tensor([[0.0, 0, 100, 100], [5, 5, 105, 105], [200, 200, 300, 300]])
    keep = nms(boxes, torch.tensor([0.9, 0.8, 0.7]), 0.55).tolist()
    assert keep == [0, 2]


@pytest.mark.skipif(not (ROOT / "models" / "person_ssdlite_xnnpack.pte").exists(), reason="model not exported")
def test_detector_runs_and_finds_no_person_in_a_blank_frame() -> None:
    pytest.importorskip("executorch")
    from PIL import Image

    from vision.detector import PersonDetector

    det = PersonDetector(ROOT / "models" / "person_ssdlite_xnnpack.pte", ROOT / "models" / "person_ssdlite_anchors.pt", "CPU")
    buf = io.BytesIO()
    Image.new("RGB", (320, 320), (128, 128, 128)).save(buf, "JPEG")
    boxes, ms = det.detect(buf.getvalue())
    assert boxes == [] and ms > 0
