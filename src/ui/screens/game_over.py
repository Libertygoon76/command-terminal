from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from src.ui import palette


class GameOverScreen(ModalScreen[None]):
    """The government has fallen. Nothing else can be done: restart the campaign or exit."""

    BINDINGS = [
        Binding("r", "restart", "Restart Campaign"),
        Binding("q,escape", "exit", "Exit"),
    ]

    def __init__(self, subject: str, body: str, victory: bool = False) -> None:
        super().__init__()
        self.subject = subject
        self.body = body
        self.victory = victory
        if victory:
            self.add_class("-victory")

    def compose(self) -> ComposeResult:
        with Vertical(id="gameover-dialog"):
            banner = f"bold #000000 on {palette.AMBER}" if self.victory else f"bold {palette.PHOSPHOR_BRIGHT} on #8b0000"
            yield Static(Text(self.subject.upper(), style=banner), id="gameover-title")
            with VerticalScroll(id="gameover-body"):
                yield Static(Text(self.body, style=palette.PHOSPHOR))
            with Horizontal(id="gameover-buttons"):
                yield Button(Text("[R] RESTART CAMPAIGN"), id="gameover-restart", variant="warning")
                yield Button(Text("[Q] EXIT TERMINAL"), id="gameover-exit")

    def on_mount(self) -> None:
        self.query_one("#gameover-dialog").border_title = (
            "★ OPERATION CONCLUDED — THE WAR IS WON ★" if self.victory else "PROTOCOL ZERO — TERMINAL LOCKED")
        self.query_one("#gameover-restart", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "gameover-restart":
            self.action_restart()
        else:
            self.action_exit()

    def action_restart(self) -> None:
        self.app.restart_campaign()

    def action_exit(self) -> None:
        self.app.exit()
