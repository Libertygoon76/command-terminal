from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from src.engine.data_loader import load_json
from src.engine.text import fill
from src.ui import palette

LINE_STYLES = {
    "title": f"bold {palette.PHOSPHOR_BRIGHT}",
    "dim": palette.PHOSPHOR_DIM,
    "ok": palette.PHOSPHOR,
    "warn": palette.AMBER,
    "alert": f"bold {palette.RED}",
    "text": palette.PHOSPHOR,
}

PROMPT = "»»  PRESS [ENTER] TO AUTHENTICATE  ««"


class BootScreen(Screen):
    """Types out the boot script from data/ui/boot_sequence.json. Any key skips; Enter proceeds."""

    def __init__(self) -> None:
        super().__init__()
        self._lines: list[dict] = load_json("ui/boot_sequence.json")["lines"]
        self._index = 0
        self._done = False
        self._log = Text()

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="boot-container"):
            yield Static(id="boot-log")
            yield Static("", id="boot-prompt")

    def on_mount(self) -> None:
        interval = float(self.app.game.config.get("boot_line_interval_seconds", 0.07))
        self._type_timer = self.set_interval(interval, self._type_next_line)

    def _type_next_line(self) -> None:
        if self._index >= len(self._lines):
            self._finish()
            return
        self._append(self._lines[self._index])
        self._index += 1
        self._render_log()

    def _append(self, line: dict) -> None:
        text = fill(line.get("text", ""), self.app.game.text_vars())
        self._log.append(text + "\n", style=LINE_STYLES.get(line.get("style", "text"), palette.PHOSPHOR))

    def _render_log(self) -> None:
        self.query_one("#boot-log", Static).update(self._log.copy())
        self.query_one("#boot-container", VerticalScroll).scroll_end(animate=False)

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._type_timer.stop()
        while self._index < len(self._lines):
            self._append(self._lines[self._index])
            self._index += 1
        self._render_log()
        self._prompt_visible = False
        self._blink()
        self.set_interval(0.55, self._blink)

    def _blink(self) -> None:
        self._prompt_visible = not self._prompt_visible
        self.query_one("#boot-prompt", Static).update(Text(PROMPT if self._prompt_visible else ""))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        if not self._done:
            self._finish()
        elif event.key == "enter":
            from src.ui.screens.terminal import TerminalScreen

            self.app.switch_screen(TerminalScreen())
