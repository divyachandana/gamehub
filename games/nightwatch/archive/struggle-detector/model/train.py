"""Train the frustration model: a tiny MLP over the 10 behavioral features.

Data sources (any mix):
    --csv data/patients.csv        columns = the 10 features in server/features.py + "label" (0/1)
    --logs logs/sessions           our own portal sessions; a window is labeled frustrated if the
                                   user pressed F8 within LABEL_WINDOW_S after it
    --synthetic 4000               generated rows, ONLY to test the pipeline end to end.
                                   Never report accuracy from synthetic data as a result.

    python model/train.py --synthetic 4000                 # pipeline smoke test
    python model/train.py --csv data/patients.csv --logs logs/sessions

Writes models/frustration.pt (weights) and prints held-out accuracy / F1.
Patient data stays in data/ (git-ignored) and never leaves this machine.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from features import FEATURES, SCALE, to_vector  # noqa: E402

LABEL_WINDOW_S = 6.0  # F8 marks the previous few seconds as frustrated


class FrustrationNet(nn.Module):
    """10 scaled features -> 1 logit. Small on purpose: ~350 parameters, sub-ms on any unit."""

    def __init__(self) -> None:
        super().__init__()
        self.net = nn.Sequential(nn.Linear(len(FEATURES), 16), nn.ReLU(), nn.Linear(16, 8), nn.ReLU(), nn.Linear(8, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def from_csv(path: Path) -> list[tuple[list[float], int]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in [*FEATURES, "label"] if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"{path} is missing columns {missing}. Map the dataset to server/features.py first.")
        for r in reader:
            rows.append((to_vector({k: float(r[k] or 0) for k in FEATURES}), int(float(r["label"]))))
    return rows


def from_logs(folder: Path) -> list[tuple[list[float], int]]:
    rows = []
    for file in sorted(folder.glob("*.jsonl")):
        records = [json.loads(line) for line in file.open(encoding="utf-8")]
        label_times = [r["t"] for r in records if r.get("type") == "label"]
        for r in records:
            if r.get("type") != "features":
                continue
            frustrated = any(0 <= lt - r["t"] <= LABEL_WINDOW_S for lt in label_times)
            rows.append((to_vector(r["features"]), int(frustrated)))
    return rows


def synthetic(n: int, seed: int = 0) -> list[tuple[list[float], int]]:
    """Plausible fake rows so the pipeline can be tested before real data is mapped."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        frustrated = rng.random() < 0.35
        k = 1.0 if frustrated else 0.25
        raw = {
            "clicks_per_s": rng.uniform(0, 1.5 + 2 * k), "rage_clicks": rng.choice([0, 0, 1, 2]) * k * 1.5,
            "dead_clicks": rng.uniform(0, 5 * k), "idle_s": rng.uniform(0, 8 + 15 * k),
            "field_reedits": rng.choice([0, 1, 2, 3]) * k, "backspaces": rng.uniform(0, 18 * k),
            "errors_shown": rng.choice([0, 1, 2, 3]) * k, "nav_back": rng.choice([0, 0, 1, 2]) * k,
            "mouse_thrash": rng.uniform(0, 5 * k), "time_on_step_s": rng.uniform(5, 60 + 200 * k),
        }
        rows.append((to_vector(raw), int(frustrated)))
    return rows


def train(rows: list[tuple[list[float], int]], epochs: int, seed: int) -> FrustrationNet:
    random.Random(seed).shuffle(rows)
    split = max(1, int(len(rows) * 0.8))
    tr, te = rows[:split], rows[split:] or rows[:1]
    x = torch.tensor([r[0] for r in tr]); y = torch.tensor([[float(r[1])] for r in tr])
    xt = torch.tensor([r[0] for r in te]); yt = torch.tensor([r[1] for r in te])

    torch.manual_seed(seed)
    model = FrustrationNet()
    pos = max(float(y.sum()), 1.0)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([(len(y) - pos) / pos]))  # balance classes
    opt = torch.optim.Adam(model.parameters(), lr=1e-2, weight_decay=1e-4)
    for _ in range(epochs):
        opt.zero_grad()
        loss = loss_fn(model(x), y)
        loss.backward()
        opt.step()

    with torch.no_grad():
        pred = (torch.sigmoid(model(xt)).flatten() >= 0.5).long()
    tp = int(((pred == 1) & (yt == 1)).sum()); fp = int(((pred == 1) & (yt == 0)).sum())
    fn = int(((pred == 0) & (yt == 1)).sum())
    acc = float((pred == yt).float().mean())
    f1 = 2 * tp / max(2 * tp + fp + fn, 1)
    print(f"train {len(tr)} rows, held-out {len(te)} rows ({int(yt.sum())} frustrated)")
    print(f"held-out accuracy {acc:.3f}  F1 {f1:.3f}  (precision {tp / max(tp + fp, 1):.3f}, recall {tp / max(tp + fn, 1):.3f})")
    return model


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", type=Path, action="append", default=[])
    p.add_argument("--logs", type=Path)
    p.add_argument("--synthetic", type=int, default=0)
    p.add_argument("--epochs", type=int, default=600)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    rows: list[tuple[list[float], int]] = []
    for path in args.csv:
        rows += from_csv(path)
    if args.logs:
        rows += from_logs(args.logs)
    if args.synthetic:
        print("WARNING: synthetic rows included; accuracy below is NOT a real result.")
        rows += synthetic(args.synthetic, args.seed)
    if len(rows) < 20:
        sys.exit("Need at least 20 labeled rows.")

    model = train(rows, args.epochs, args.seed)
    out = ROOT / "models" / "frustration.pt"
    out.parent.mkdir(exist_ok=True)
    torch.save(model.state_dict(), out)
    print(f"saved {out}")


assert set(SCALE) == set(FEATURES)

if __name__ == "__main__":
    main()
