"""The Neural Court (Expansion 2.0): a free-text conversation with a courtier (Royal Court screen, T).

The Lord Protector types; the courtier answers through the local model (Ollama) or, when it is not running, with a
scripted line. The model call runs in a worker thread so the terminal never freezes; the answer is applied to the
game state back on the UI thread.
"""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from src.engine import neural_engine
from src.ui import palette


class ConverseScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, character_id: str) -> None:
        super().__init__()
        self.character_id = character_id
        self.waiting = False

    def compose(self) -> ComposeResult:
        with Vertical(id="converse-dialog"):
            yield Static(id="converse-status")
            with VerticalScroll(id="converse-log"):
                yield Static(id="converse-transcript")
            yield Input(placeholder="Speak, Lord Protector… (Enter to speak, Esc to leave)", id="converse-input")

    def on_mount(self) -> None:
        char = self.app.game.player.dynasty.characters[self.character_id]
        self.query_one("#converse-dialog").border_title = f"PRIVATE AUDIENCE — {char.name.upper()}"
        self._redraw()
        self.query_one("#converse-input", Input).focus()
        self._probe()

    @work(thread=True, exclusive=True, group="probe")
    def _probe(self) -> None:
        neural_engine.status(self.app.game)  # the sub-second ping, off the UI thread
        self.app.call_from_thread(self._redraw)

    def _redraw(self, note: str = "") -> None:
        game = self.app.game
        char = game.player.dynasty.characters[self.character_id]
        info = neural_engine.cached_status(game)
        status = Text()
        status.append(f"{char.name.upper()} · {char.role} · LOYALTY {char.loyalty:.0f}   ", style=palette.PHOSPHOR_BRIGHT)
        if info["online"]:
            status.append(f"NEURAL COURT ONLINE ({info['model']})", style=palette.PHOSPHOR)
        elif info["online"] is None:
            status.append("NEURAL COURT: CHECKING…", style=palette.PHOSPHOR_DIM)
        else:
            status.append("NEURAL COURT OFFLINE — scripted replies", style=palette.AMBER)
            status.append(f"\n{info['reason']}", style=palette.PHOSPHOR_DIM)
        self.query_one("#converse-status", Static).update(status)
        log = Text()
        for entry in game.player.dynasty.conversations.get(self.character_id, []):
            log.append(f"WK {entry['turn']:03d} LORD PROTECTOR: ", style=f"bold {palette.AMBER}")
            log.append(entry["player"] + "\n", style=palette.PHOSPHOR_BRIGHT)
            log.append(f"{char.name.upper()}: ", style=f"bold {palette.PHOSPHOR}")
            log.append(entry["reply"], style=palette.PHOSPHOR)
            if entry.get("applied"):
                log.append(f"  [LOYALTY {entry['applied']:+d}]", style=palette.AMBER)
            log.append("\n\n")
        if note:
            log.append(note, style=palette.PHOSPHOR_DIM)
        self.query_one("#converse-transcript", Static).update(log)
        self.query_one("#converse-log", VerticalScroll).scroll_end(animate=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self.waiting or not event.value.strip():
            return
        try:
            exchange = neural_engine.prepare_conversation(self.app.game, self.character_id, event.value)
        except neural_engine.ConverseError as error:
            self.notify(str(error), title="PRIVATE AUDIENCE", severity="warning")
            return
        event.input.value = ""
        self.waiting = True
        self._redraw(note="…")
        self._ask(exchange)

    @work(thread=True, exclusive=True)
    def _ask(self, exchange: neural_engine.Exchange) -> None:
        result, reason = neural_engine.try_fetch(exchange)  # the only slow part: no game state is touched here
        self.app.call_from_thread(self._answered, exchange, result, reason)

    def _answered(self, exchange: neural_engine.Exchange, result, reason: str) -> None:
        self.waiting = False
        try:
            neural_engine.complete_conversation(self.app.game, exchange, result, reason)
        except neural_engine.ConverseError as error:
            self.notify(str(error), title="PRIVATE AUDIENCE", severity="warning")
        self.app.state_changed()
        self._redraw()

    def action_close(self) -> None:
        self.dismiss(None)
