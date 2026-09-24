from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from src.ui import palette
from src.ui.palette import label_value


class StatusBar(Horizontal):
    """Top bar: nation, date/turn, and the headline national stats."""

    def compose(self) -> ComposeResult:
        yield Static(id="sb-title")
        yield Static(id="sb-date", classes="sb-cell")
        yield Static(id="sb-treasury", classes="sb-cell")
        yield Static(id="sb-manpower", classes="sb-cell")
        yield Static(id="sb-morale", classes="sb-cell")

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        game = self.app.game
        nation = game.player

        title = Text()
        title.append("▣ ", style=palette.AMBER)
        title.append(game.config.get("game_title", "COMMAND TERMINAL"), style=f"bold {palette.PHOSPHOR_BRIGHT}")
        title.append(f"  //  {nation.name.upper()}", style=palette.PHOSPHOR_DIM)
        self.query_one("#sb-title", Static).update(title)

        self.query_one("#sb-date", Static).update(
            label_value("DATE", f"{game.clock.date_str}  WK {game.clock.turn:03d}")
        )

        treasury_style = palette.RED if nation.in_debt else palette.PHOSPHOR_BRIGHT
        self.query_one("#sb-treasury", Static).update(
            label_value("TREASURY", palette.money(nation.treasury, game.currency), treasury_style)
        )
        self.query_one("#sb-manpower", Static).update(label_value("MANPOWER", f"{nation.manpower:,}"))

        band_style = palette.MORALE_STYLE.get(nation.morale_band, palette.PHOSPHOR)
        morale = Text(f"{nation.morale:.0f}% {palette.meter(nation.morale)}", style=band_style)
        self.query_one("#sb-morale", Static).update(label_value("MORALE", morale))
