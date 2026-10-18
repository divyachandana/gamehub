"""Person detection on one camera frame with the ExecuTorch SSDlite model.

The .pte holds the network; this file does the light work around it in plain PyTorch
(no torchvision needed at runtime): resize + normalize, decode boxes against the saved
anchors, keep "person", non-maximum suppression. Frames are processed in memory only.
"""
from __future__ import annotations

import io
import math
import time
from pathlib import Path

import torch

SIZE = 320
PERSON = 1                      # COCO category index for "person" in torchvision's label map
BOX_WEIGHTS = (10.0, 10.0, 5.0, 5.0)
CLAMP = math.log(1000.0 / 16)


class PersonDetector:
    def __init__(self, pte: Path, anchors: Path, device: str, threshold: float = 0.5) -> None:
        from executorch.runtime import Runtime

        self._method = Runtime.get().load_program(str(pte)).load_method("forward")
        self._anchors = torch.load(anchors, weights_only=True)
        self.name = f"ExecuTorch {pte.name}"
        self.device = device
        self.threshold = threshold

    @staticmethod
    def preprocess(jpeg: bytes) -> torch.Tensor:
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg)).convert("RGB").resize((SIZE, SIZE))
        x = torch.frombuffer(bytearray(img.tobytes()), dtype=torch.uint8).view(SIZE, SIZE, 3)
        # .contiguous(): ExecuTorch reads the raw buffer, so a permuted view would scramble channels.
        return x.permute(2, 0, 1).float().div(255).sub(0.5).div(0.5).unsqueeze(0).contiguous()

    def run(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, float]:
        start = time.perf_counter()
        deltas, logits = self._method.execute([x])
        return deltas[0], logits[0], (time.perf_counter() - start) * 1000

    def detect(self, jpeg: bytes, label: int = PERSON) -> tuple[list[dict[str, float]], float]:
        """Return person boxes as fractions of the frame (x1, y1, x2, y2, score) and model ms."""
        deltas, logits, ms = self.run(self.preprocess(jpeg))
        return self.postprocess(deltas, logits, label), ms

    def postprocess(self, deltas: torch.Tensor, logits: torch.Tensor, label: int) -> list[dict[str, float]]:
        scores = torch.softmax(logits, dim=-1)[:, label]
        keep = scores > self.threshold
        if not keep.any():
            return []
        boxes = decode(deltas[keep], self._anchors[keep])
        idx = nms(boxes, scores[keep], 0.55)
        return [
            {"x1": float(b[0]) / SIZE, "y1": float(b[1]) / SIZE, "x2": float(b[2]) / SIZE, "y2": float(b[3]) / SIZE,
             "score": round(float(s), 3)}
            for b, s in zip(boxes[idx].clamp(0, SIZE), scores[keep][idx])
        ]


def decode(rel: torch.Tensor, anchors: torch.Tensor) -> torch.Tensor:
    """torchvision BoxCoder.decode_single with SSD weights, in plain torch."""
    wx, wy, ww, wh = BOX_WEIGHTS
    widths, heights = anchors[:, 2] - anchors[:, 0], anchors[:, 3] - anchors[:, 1]
    cx, cy = anchors[:, 0] + 0.5 * widths, anchors[:, 1] + 0.5 * heights
    dx, dy = rel[:, 0] / wx, rel[:, 1] / wy
    dw, dh = (rel[:, 2] / ww).clamp(max=CLAMP), (rel[:, 3] / wh).clamp(max=CLAMP)
    pcx, pcy = dx * widths + cx, dy * heights + cy
    pw, ph = torch.exp(dw) * widths, torch.exp(dh) * heights
    return torch.stack([pcx - 0.5 * pw, pcy - 0.5 * ph, pcx + 0.5 * pw, pcy + 0.5 * ph], dim=1)


def nms(boxes: torch.Tensor, scores: torch.Tensor, iou: float) -> torch.Tensor:
    order = scores.argsort(descending=True)
    keep: list[int] = []
    while order.numel():
        i = int(order[0])
        keep.append(i)
        if order.numel() == 1:
            break
        rest = order[1:]
        x1 = torch.maximum(boxes[i, 0], boxes[rest, 0]); y1 = torch.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = torch.minimum(boxes[i, 2], boxes[rest, 2]); y2 = torch.minimum(boxes[i, 3], boxes[rest, 3])
        inter = (x2 - x1).clamp(min=0) * (y2 - y1).clamp(min=0)
        area = lambda b: (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])  # noqa: E731
        union = area(boxes[i:i + 1]) + area(boxes[rest]) - inter
        order = rest[inter / union <= iou]
    return torch.tensor(keep, dtype=torch.long)
