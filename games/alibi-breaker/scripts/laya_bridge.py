"""Laya on the NPU as a JSON-lines service on stdin/stdout, for the stress meter (how rattled a suspect sounds).

Laya's host code needs PyTorch + transformers (for tokenization), which the game's own
environment doesn't have, so the game runs this script with the Python that has them:

    <python> scripts/laya_bridge.py <laya_x_elite folder>

Protocol, one JSON object per line:
    -> {"state": "...", "question": {"type": "noul", "instructions": "..."}}
    <- Laya's answer dict (e.g. {"type": "noul", "noul": 0.81, "npu_ms": 303.0})
    -> {"cmd": "quit"}
The first line written is {"ready": true} once the model is loaded.
"""

import json
import sys


def main() -> None:
    """Load Laya once, then answer one question per input line until told to quit."""
    sys.path.insert(0, sys.argv[1])
    from laya_host import LayaHost  # noqa: PLC0415 - path is only known at runtime

    with LayaHost() as laya:
        print(json.dumps({"ready": True}), flush=True)
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("cmd") == "quit":
                break
            try:
                answer = laya.ask(request["state"], request["question"])
            except Exception as exc:  # noqa: BLE001 - report to the game instead of dying
                answer = {"error": str(exc)}
            print(json.dumps(answer), flush=True)


if __name__ == "__main__":
    main()
