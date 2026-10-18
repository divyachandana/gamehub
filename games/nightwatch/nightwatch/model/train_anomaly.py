"""Train the "is this normal for right now?" model over the 8 live features.

Method: a tiny classifier learns to tell real normal moments apart from scrambled ones
(each feature shuffled independently, which keeps every single value plausible but breaks the
combinations). It ends up scoring *combinations*: walking the corridor is normal at 2 pm and
unusual at 2 am; getting up at 3 am is normal for 5 minutes in the ensuite, unusual at an exit.
No labeled incidents needed, which matters because real elopement data is rare.

    python nightwatch/model/train_anomaly.py

In this hackathon build, "normal" comes from SIMULATED days. In a facility it would be each
resident's own first weeks of sensor data, learned on the device.
Writes models/normal_pattern.pt (+ .json with notes). Output: logit that the moment is normal.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import torch
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "nightwatch"))
from features import FEATURES, vector  # noqa: E402


class NormalPattern(nn.Module):
    """8 -> 32 -> 16 -> 1 logit ("this moment looks normal"). About 850 parameters."""

    def __init__(self) -> None:
        super().__init__()
        n = len(FEATURES)
        self.net = nn.Sequential(nn.Linear(n, 32), nn.ReLU(), nn.Linear(32, 16), nn.ReLU(), nn.Linear(16, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def normal_samples(n: int, rng: random.Random) -> list[list[float]]:
    """Simulated normal behavior: active days, settled nights, short ensuite trips at night."""
    out = []
    for _ in range(n):
        hour = rng.uniform(0, 24)
        day = 7 <= hour < 21
        settling = 5 <= hour < 7 or 21 <= hour < 22  # getting up / going to bed
        if day or (settling and rng.random() < 0.5):
            up = rng.random() < 0.85
            zone = rng.choices(["room", "corridor", "common", "exit"], [0.4, 0.3, 0.27, 0.03])[0] if up else "room"
            out.append(vector(hour, up, rng.uniform(0.15, 0.7) if up else rng.uniform(0, 0.1),
                              70 + rng.gauss(5, 6), 70, zone, _minutes_up(rng, 30) if up else 0))
        elif rng.random() < 0.12:  # short trip to the ensuite bathroom
            out.append(vector(hour, True, rng.uniform(0.05, 0.45), 70 + rng.gauss(3, 6), 70, "room",
                              _minutes_up(rng, 10)))
        else:  # asleep / resting in bed
            out.append(vector(hour, False, rng.uniform(0, 0.08), 70 + rng.gauss(-2, 6), 70, "room", 0))
    return out


def _minutes_up(rng: random.Random, longest: float) -> float:
    """Minutes since getting up. A third of samples are the first minute: just got up is normal."""
    return rng.uniform(0, 1) if rng.random() < 0.33 else rng.uniform(0, longest)


def scrambled(x: torch.Tensor, gen: torch.Generator) -> torch.Tensor:
    """Shuffle each feature column independently: plausible values, implausible combinations."""
    cols = [x[torch.randperm(len(x), generator=gen), i] for i in range(x.shape[1])]
    out = torch.stack(cols, dim=1)
    # Keep hour_sin/hour_cos together so the time stays a real time of day.
    idx = torch.randperm(len(x), generator=gen)
    out[:, 0], out[:, 1] = x[idx, 0], x[idx, 1]
    return out


PROBES = {
    "2:12 am, up 12 min, at an exit, HR +30": (2.2, True, 0.8, 100, 70, "exit", 12),
    "2:12 am, in the corridor, HR +15": (2.2, True, 0.5, 85, 70, "corridor", 5),
    "3:00 am, short ensuite trip": (3.0, True, 0.3, 73, 70, "room", 3),
    "3:00 am, asleep": (3.0, False, 0.02, 66, 70, "room", 0),
    "3:00 am, just got up (0 min)": (3.0, True, 0.25, 69, 70, "room", 0.05),
    "3:00 am, asleep, HR +6": (3.0, False, 0.03, 76, 70, "room", 0),
    "2:00 pm, walking the corridor": (14.0, True, 0.5, 76, 70, "corridor", 20),
    "2:00 pm, by the front door": (14.0, True, 0.4, 75, 70, "exit", 10),
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=20, help="simulated days (x 1440 one-minute samples)")
    p.add_argument("--epochs", type=int, default=1500)
    args = p.parse_args()

    rng = random.Random(7)
    gen = torch.Generator().manual_seed(7)
    torch.manual_seed(7)
    x = torch.tensor(normal_samples(args.days * 1440, rng))
    model = NormalPattern()
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    loss_fn = nn.BCEWithLogitsLoss()
    for _ in range(args.epochs):
        neg = scrambled(x, gen)
        batch = torch.cat([x, neg])
        labels = torch.cat([torch.ones(len(x), 1), torch.zeros(len(neg), 1)])
        opt.zero_grad()
        loss = loss_fn(model(batch), labels)
        loss.backward()
        opt.step()
    print(f"trained on {len(x)} simulated normal samples vs scrambled, final loss {loss.item():.4f}")

    with torch.no_grad():
        for name, args_ in PROBES.items():
            unusual = float(torch.sigmoid(-model(torch.tensor([vector(*args_)]))))
            print(f"  unusual {unusual:4.2f}  {name}")

    out = ROOT / "models"
    out.mkdir(exist_ok=True)
    torch.save(model.state_dict(), out / "normal_pattern.pt")
    (out / "normal_pattern.json").write_text(json.dumps(
        {"features": FEATURES, "output": "logit that the moment is normal; unusual = sigmoid(-logit)",
         "trained_on": "simulated normal days vs feature-shuffled negatives"}, indent=2))
    print(f"saved {out / 'normal_pattern.pt'}")


if __name__ == "__main__":
    main()
