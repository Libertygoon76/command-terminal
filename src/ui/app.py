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

    def __init__(self, skip_boot: bool = False, seed: int | None = None, reveal: bool = False,
                 event: str | None = None, load: str | None = None, save_path: str | None = None,
                 crisis: str | None = None) -> None:
        super().__init__()
        self._reveal = reveal
        self._forced_event = event
        self.save_path = save_path or load  # where CTRL+S writes (default: savegame.json in the game folder)
        if load is not None:
            from src.engine.savegame import load_game

            self.game = load_game(load or None)
            if self._reveal:
                self.game.config.setdefault("map", {})["debug_reveal_all"] = True
            self.tick_engine = build_default_engine(self.game)
        else:
            self._new_campaign(seed)
        if crisis:  # main.py --crisis: forced at the next week
            self.game.flags["forced_crisis"] = crisis
        self._skip_boot = skip_boot

    def _new_campaign(self, seed: int | None = None) -> None:
        self.game = new_game(seed=seed)
        if self._reveal:
            self.game.config.setdefault("map", {})["debug_reveal_all"] = True
        if self._forced_event:  # main.py --event: this card is drawn when the first week is advanced
            from src.engine.dilemmas import card

            card(self.game, self._forced_event)  # fail fast on a typo
            self.game.forced_card = self._forced_event
            self._forced_event = None
        self.tick_engine = build_default_engine(self.game)

    def restart_campaign(self) -> None:
        """After a fall: a fresh campaign on a fresh desktop."""
        self._new_campaign()
        while len(self.screen_stack) > 2:  # drop modals above the desktop
            self.pop_screen()
        self.switch_screen(TerminalScreen())
        self.notify("A new government takes office.", title="CAMPAIGN RESTARTED")

    def state_changed(self) -> None:
        self.revision += 1

    def on_mount(self) -> None:
        self.register_theme(TERMINAL_THEME)
        self.theme = TERMINAL_THEME.name
        self.push_screen(TerminalScreen() if self._skip_boot else BootScreen())
