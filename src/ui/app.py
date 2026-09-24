from __future__ import annotations

from textual.app import App
from textual.reactive import reactive
from textual.theme import Theme

from src.engine.data_loader import new_game
from src.engine.tick_engine import build_default_engine
from src.ui import palette
from src.ui.screens.boot import BootScreen
from src.ui.screens.terminal import TerminalScreen

TERMINAL_THEME = Theme(
    name="command-terminal",
    primary=palette.PHOSPHOR,
    secondary=palette.PHOSPHOR_DIM,
    accent=palette.AMBER,
    warning=palette.AMBER,
    error=palette.RED,
    success=palette.PHOSPHOR,
    foreground=palette.PHOSPHOR,
    background=palette.BACKGROUND,
    surface=palette.SURFACE,
    panel=palette.PANEL,
    dark=True,
)


class CommandTerminalApp(App):
    """The secure terminal. Owns the GameState and TickEngine; screens read from them.

    `revision` is the single reactive signal for "the game state changed". Anything that
    mutates state through the engine calls `state_changed()`, and every widget that
    displays state watches `revision` and redraws itself.
    """

    CSS_PATH = "styles/terminal.tcss"
    TITLE = "COMMAND TERMINAL"
    ENABLE_COMMAND_PALETTE = False

    revision: reactive[int] = reactive(0)

    def __init__(self, skip_boot: bool = False, seed: int | None = None, reveal: bool = False) -> None:
        super().__init__()
        self.game = new_game(seed=seed)
        if reveal:
            self.game.config.setdefault("map", {})["debug_reveal_all"] = True
        self.tick_engine = build_default_engine(self.game)
        self._skip_boot = skip_boot

    def state_changed(self) -> None:
        self.revision += 1

    def on_mount(self) -> None:
        self.register_theme(TERMINAL_THEME)
        self.theme = TERMINAL_THEME.name
        self.push_screen(TerminalScreen() if self._skip_boot else BootScreen())
