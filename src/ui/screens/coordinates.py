from __future__ import annotations

import re

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from src.ui import palette

COORDS = re.compile(r"^\s*(\d{1,3})\s*[,\s-]\s*(\d{1,3})\s*$")


def parse_coordinates(text: str) -> tuple[int, int] | None:
    """Accept '72,15', '72 15' or grid style '072-015'."""
    match = COORDS.match(text)
    return (int(match.group(1)), int(match.group(2))) if match else None


class CoordinatesScreen(ModalScreen[tuple[int, int] | None]):
    """Type an exact map grid reference for a move order."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, unit_label: str, width: int, height: int, initial: tuple[int, int]) -> None:
        super().__init__()
        self.unit_label = unit_label
        self.map_width = width
        self.map_height = height
        self.initial = initial

    def compose(self) -> ComposeResult:
        text = Text()
        text.append("MOVE ORDER — GRID REFERENCE\n\n", style=f"bold {palette.AMBER}")
        text.append("UNIT: ", style=palette.PHOSPHOR_DIM)
        text.append(f"{self.unit_label}\n", style=palette.PHOSPHOR_BRIGHT)
        text.append(f"Enter X,Y (X 0–{self.map_width - 1}, Y 0–{self.map_height - 1}). ", style=palette.PHOSPHOR_DIM)
        text.append("Examples: 72,15  or  072-015", style=palette.PHOSPHOR_DIM)
        with Vertical(id="coords-dialog"):
            yield Static(text)
            yield Input(value=f"{self.initial[0]},{self.initial[1]}", placeholder="X,Y", id="coords-input")
            yield Static("", id="coords-error")

    def on_mount(self) -> None:
        self.query_one("#coords-dialog").border_title = "SECURE ORDER ENTRY"
        field = self.query_one("#coords-input", Input)
        field.focus()
        field.action_select_all()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        coords = parse_coordinates(event.value)
        if coords is None or not (0 <= coords[0] < self.map_width and 0 <= coords[1] < self.map_height):
            self.query_one("#coords-error", Static).update(
                Text("INVALID GRID REFERENCE.", style=f"bold {palette.RED}")
            )
            return
        self.dismiss(coords)

    def action_cancel(self) -> None:
        self.dismiss(None)
