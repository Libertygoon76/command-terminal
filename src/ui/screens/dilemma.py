from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from src.engine.dilemmas import card, card_text, choice_preview
from src.ui import palette


class DilemmaScreen(ModalScreen[str]):
    """A CLASSIFIED DILEMMA: an event card that must be answered before the week can advance.

    There is no escape key: the Lord Protector must decide. 1 / 2 / 3 (or the buttons) choose.
    Dismisses with the chosen option id.
    """

    BINDINGS = [
        Binding("1", "choose(0)", "Option 1", show=False),
        Binding("2", "choose(1)", "Option 2", show=False),
        Binding("3", "choose(2)", "Option 3", show=False),
    ]

    def __init__(self, card_id: str) -> None:
        super().__init__()
        self.card_id = card_id

    def compose(self) -> ComposeResult:
        game = self.app.game
        entry = card(game, self.card_id)
        self.choices = entry["choices"]
        header = Text()
        header.append(f" {entry.get('category', 'CLASSIFIED')} ", style=f"bold #000000 on {palette.AMBER}")
        header.append(f"  WEEK {game.clock.turn:03d} · {game.clock.date_str}", style=palette.PHOSPHOR_DIM)
        with Vertical(id="dilemma-dialog"):
            yield Static(header, id="dilemma-meta")
            yield Static(Text(entry["title"].upper(), style=f"bold {palette.PHOSPHOR_BRIGHT}"), id="dilemma-title")
            with VerticalScroll(id="dilemma-body"):
                yield Static(Text(card_text(game, entry), style=palette.PHOSPHOR))
            with Horizontal(id="dilemma-choices"):
                for index, choice in enumerate(self.choices):
                    label = Text()
                    label.append(f"[{index + 1}] {choice['label'].upper()}\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
                    if choice.get("hint"):
                        label.append(choice["hint"] + "\n", style=palette.PHOSPHOR)
                    effects = choice_preview(game, choice)
                    label.append("\n".join(effects) if effects else "No immediate cost.", style=palette.AMBER)
                    yield Button(label, id=f"dilemma-{choice['id']}", classes="dilemma-choice")
            yield Static(Text("The week cannot advance until you decide. Press 1-" + str(len(self.choices))
                              + " or click an option.", style=palette.PHOSPHOR_DIM), id="dilemma-footer")

    def on_mount(self) -> None:
        self.query_one("#dilemma-dialog").border_title = "▲ CLASSIFIED DILEMMA — EYES ONLY"
        self.query(Button).first().focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id.removeprefix("dilemma-"))

    def action_choose(self, index: int) -> None:
        if index < len(self.choices):
            self.dismiss(self.choices[index]["id"])
