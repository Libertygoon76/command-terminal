from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from src.models import Email, EmailOption
from src.ui import palette


class ConfirmReplyScreen(ModalScreen[bool]):
    """Transmission confirmation. Replies are irreversible, so they are never one keystroke."""

    BINDINGS = [
        Binding("y", "transmit", "Transmit"),
        Binding("n,escape", "abort", "Abort"),
    ]

    def __init__(self, email: Email, option: EmailOption, letter: str) -> None:
        super().__init__()
        self.email = email
        self.option = option
        self.letter = letter

    def compose(self) -> ComposeResult:
        text = Text()
        text.append("TRANSMIT REPLY?\n\n", style=f"bold {palette.AMBER}")
        text.append("RE:   ", style=palette.PHOSPHOR_DIM)
        text.append(self.email.subject.upper() + "\n", style=palette.PHOSPHOR_BRIGHT)
        text.append("SEND: ", style=palette.PHOSPHOR_DIM)
        text.append(f"[{self.letter}] {self.option.label}\n\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
        text.append("Once transmitted, an order cannot be recalled.", style=palette.PHOSPHOR_DIM)
        with Vertical(id="confirm-dialog"):
            yield Static(text, id="confirm-text")
            with Horizontal(id="confirm-buttons"):
                yield Button(Text("[Y] TRANSMIT"), id="confirm-yes", variant="warning")
                yield Button(Text("[N] ABORT"), id="confirm-no")

    def on_mount(self) -> None:
        self.query_one("#confirm-dialog").border_title = "SECURE TRANSMISSION"
        self.query_one("#confirm-yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")

    def action_transmit(self) -> None:
        self.dismiss(True)

    def action_abort(self) -> None:
        self.dismiss(False)
