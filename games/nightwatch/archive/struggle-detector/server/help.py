"""Plain-language help, chosen from what the user is stuck on.

Written by hand on purpose: help for patients must be correct every time, so the model
decides *when* to help and these messages decide *what* to say.
"""
from __future__ import annotations

# Keyed by the last validation error the page showed (most specific first).
BY_ERROR: dict[str, str] = {
    "dob_format": "Type your birthday with dashes, like 03-14-1958 (month-day-year).",
    "rx_number": "Your prescription number is on the pill bottle label. It starts with RX and has 7 numbers, like RX1234567.",
    "pharmacy": "Pick your pharmacy from the list. If it isn't there, choose 'Other' and type its name.",
    "consent": "Scroll down a little: there's a box to tick that says you agree. Then press submit.",
}

# The element to highlight for each error.
ERROR_TARGET: dict[str, str] = {"dob_format": "dob", "rx_number": "rx", "pharmacy": "pharmacy", "consent": "consent"}

# Keyed by the field the user is on when no error explains it.
BY_FIELD: dict[str, str] = {
    "dob": "Your birthday goes here, with dashes: month-day-year, like 03-14-1958.",
    "rx": "Look at the label on your pill bottle for a number starting with RX.",
    "pharmacy": "Choose the pharmacy where you usually pick up your medicine.",
    "pickup": "Pick a day you can get to the pharmacy. Weekdays are faster.",
    "consent": "Almost done. Tick the box at the bottom, then press submit.",
}

# When we don't know what's wrong, just slow things down.
GENERIC = "Take your time. You're doing fine. Look for a small red message under one of the boxes; that's the one to fix."


def pick_help(field: str | None, error: str | None) -> tuple[str, str | None]:
    """Return (message, element id to highlight)."""
    if error and error in BY_ERROR:
        return BY_ERROR[error], ERROR_TARGET.get(error)
    if field and field in BY_FIELD:
        return BY_FIELD[field], field
    return GENERIC, None
