"""Colors and small text-formatting helpers shared across the UI."""

from __future__ import annotations

from rich.text import Text

PHOSPHOR = "#33ff66"
PHOSPHOR_DIM = "#1f8f3f"
PHOSPHOR_BRIGHT = "#eaffea"
AMBER = "#ffb000"
RED = "#ff3b3b"
CYAN = "#4fd8ff"
SEA = "#1d5f7a"
BACKGROUND = "#040804"
SURFACE = "#081008"
PANEL = "#0c1a0e"

CLASSIFICATION_STYLE = {
    "UNCLASSIFIED": f"bold {PHOSPHOR_DIM}",
    "CONFIDENTIAL": f"bold {AMBER}",
    "SECRET": f"bold {RED}",
    "TOP SECRET": f"bold {PHOSPHOR_BRIGHT} on #8b0000",
}

CLASSIFICATION_TAG = {
    "UNCLASSIFIED": "U",
    "CONFIDENTIAL": "C",
    "SECRET": "S",
    "TOP SECRET": "TS",
}

MORALE_STYLE = {
    "COLLAPSING": f"bold {RED}",
    "UNREST": AMBER,
    "STEADY": PHOSPHOR,
    "HIGH": f"bold {PHOSPHOR_BRIGHT}",
}


def money(amount: int, currency: str) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}{abs(amount):,} {currency}"


def meter(value: float, maximum: float = 100.0, width: int = 10) -> str:
    filled = round(width * max(0.0, min(value, maximum)) / maximum)
    return "▮" * filled + "▯" * (width - filled)


def label_value(label: str, value: str | Text, value_style: str = PHOSPHOR_BRIGHT) -> Text:
    text = Text()
    text.append(f"{label} ", style=PHOSPHOR_DIM)
    if isinstance(value, Text):
        text.append_text(value)
    else:
        text.append(value, style=value_style)
    return text
