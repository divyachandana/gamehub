"""Summarize de-identified care records into a resident risk profile (aggregates only).

    python tools/build_profile.py "path/to/Patient 1 Backend Data.xlsx" --out data/profiles

Reads the Event_Records sheet (taxonomy v2) and writes one JSON per patient with counts only:
how often they wandered or sought the exit, when, after which triggers, in what state,
and which non-drug approaches were followed by a settled/managed outcome.

The source file and the output stay in data/ (git-ignored). Never commit either.
Needs: pip install openpyxl
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

WANDER = {"Exit-seeking or wandering"}
GOOD_OUTCOMES = {"Managed without further escalation", "Care completed"}
GOOD_RESPONSES = {"Behaviour managed", "Settled", "Care accepted or completed"}


def load_rows(path: Path) -> list[dict[str, object]]:
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)["Event_Records"]
    rows = list(ws.iter_rows(values_only=True))
    header_at = next(i for i, r in enumerate(rows) if r and r[0] == "patient")
    header = rows[header_at]
    return [dict(zip(header, r)) for r in rows[header_at + 1:] if r and r[0]]


def profile(events: list[dict[str, object]]) -> dict[str, object]:
    wander = [e for e in events if e["behaviour"] in WANDER]
    helped: Counter[str] = Counter()
    tried: Counter[str] = Counter()
    for e in events:
        for key in ("nondrug_alt", "intervention"):
            approach = e.get(key)
            if not approach or approach in ("Recorded care interaction", "Monitoring"):
                continue
            tried[str(approach)] += 1
            if e.get("outcome") in GOOD_OUTCOMES or e.get("response") in GOOD_RESPONSES:
                helped[str(approach)] += 1
    by_phase: dict[str, int] = defaultdict(int)
    for e in wander:
        by_phase[str(e["phase_display"])] += 1
    return {
        "events": len(events),
        "wander_events": len(wander),
        "wander_share": round(len(wander) / max(len(events), 1), 3),
        "wander_by_time_of_day": dict(Counter(str(e["time_of_day"]) for e in wander)),
        "wander_by_phase": dict(by_phase),
        "wander_affect": dict(Counter(str(e["affect"]) for e in wander if e["affect"])),
        "triggers": dict(Counter(str(e["trigger"]) for e in events if e["trigger"])),
        "escalated_for_review": sum(1 for e in events if e["outcome"] == "Escalated for clinical review"),
        "severity": dict(Counter(int(e["severity"]) for e in events if e["severity"] is not None)),
        "what_helped": [
            {"approach": a, "helped": helped[a], "tried": tried[a]}
            for a, _ in sorted(tried.items(), key=lambda kv: (-helped[kv[0]] / kv[1], -kv[1]))
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("xlsx", type=Path)
    p.add_argument("--out", type=Path, default=Path("data/profiles"))
    args = p.parse_args()
    by_patient: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in load_rows(args.xlsx):
        by_patient[str(row["patient"])].append(row)
    args.out.mkdir(parents=True, exist_ok=True)
    for patient, events in by_patient.items():
        out = args.out / f"{patient}.json"
        out.write_text(json.dumps(profile(events), indent=2))
        print(f"{patient}: {len(events)} events -> {out}")


if __name__ == "__main__":
    main()
