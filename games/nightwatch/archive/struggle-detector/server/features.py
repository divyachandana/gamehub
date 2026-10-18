"""The feature contract shared by the browser tracker, the scorers and the training script.

The tracker (portal/tracker.js) sends these once per second, computed over a sliding
10-second window. Order matters: it is the input order of the frustration model.
Keep FEATURES here and FEATURE_NAMES in tracker.js identical.
"""
from __future__ import annotations

FEATURES: list[str] = [
    "clicks_per_s",      # all clicks / window seconds
    "rage_clicks",       # bursts of 3+ clicks within 1 s inside a 30 px radius
    "dead_clicks",       # clicks on things that do nothing (not a control, label or link)
    "idle_s",            # seconds since the last mouse/keyboard input (capped at 30)
    "field_reedits",     # fields edited more than twice in this step
    "backspaces",        # backspace/delete presses in the window
    "errors_shown",      # validation errors displayed in the window
    "nav_back",          # times the user went back a step in the window
    "mouse_thrash",      # mouse direction reversals per second
    "time_on_step_s",    # seconds spent on the current step (capped at 300)
]

# Rough upper bounds used to scale raw values into ~[0, 1] before the model sees them.
# The same scaling is applied at training time (model/train.py imports this).
SCALE: dict[str, float] = {
    "clicks_per_s": 4.0,
    "rage_clicks": 3.0,
    "dead_clicks": 6.0,
    "idle_s": 30.0,
    "field_reedits": 4.0,
    "backspaces": 20.0,
    "errors_shown": 4.0,
    "nav_back": 3.0,
    "mouse_thrash": 6.0,
    "time_on_step_s": 300.0,
}


def to_vector(raw: dict[str, float]) -> list[float]:
    """Scale a raw feature dict into the model's input vector (missing values = 0)."""
    return [min(float(raw.get(name, 0.0)) / SCALE[name], 1.5) for name in FEATURES]
