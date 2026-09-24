from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import var
from textual.timer import Timer
from textual.widgets import Static

from src.models.nation import morale_band
from src.ui import palette
from src.ui.palette import label_value

FLASH_SECONDS = 2.5


class StatusBar(Horizontal):
    """Top bar: nation, date/turn, and the headline national stats.

    Each stat is a reactive `var`. On every app revision the bar copies values from the
    game state; only stats that actually changed fire their watcher, which redraws that
    cell and briefly flashes the delta (green up / red down).
    """

    turn = var(0)
    treasury = var(0)
    manpower = var(0)
    morale = var(0.0)
    military_morale = var(0.0)
    fallen = var(False)

    _stats_ready = False

    def compose(self) -> ComposeResult:
        yield Static(id="sb-title")
        yield Static(id="sb-date", classes="sb-cell")
        yield Static(id="sb-weather", classes="sb-cell")
        yield Static(id="sb-treasury", classes="sb-cell")
        yield Static(id="sb-manpower", classes="sb-cell")
        yield Static(id="sb-morale", classes="sb-cell")
        yield Static(id="sb-milmorale", classes="sb-cell")

    def on_mount(self) -> None:
        self._flash_deltas: dict[str, float] = {}
        self._flash_timers: dict[str, Timer] = {}
        self._sync()
        self._stats_ready = True
        self._render_all()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self._sync()

    def _sync(self) -> None:
        game = self.app.game
        nation = game.player
        self.turn = game.clock.turn
        self.treasury = nation.treasury
        self.manpower = nation.manpower
        self.morale = nation.morale
        self.military_morale = nation.military_morale
        self.fallen = game.game_over is not None

    # --- watchers ------------------------------------------------------------

    def _changed(self, key: str, old: float, new: float, flash: bool = True) -> None:
        if not self._stats_ready:
            return
        if flash and old != new:
            self._flash(key, new - old)
        self._render_all()

    def watch_turn(self, old: int, new: int) -> None:
        self._changed("turn", old, new, flash=False)

    def watch_treasury(self, old: int, new: int) -> None:
        self._changed("treasury", old, new)

    def watch_manpower(self, old: int, new: int) -> None:
        self._changed("manpower", old, new)

    def watch_morale(self, old: float, new: float) -> None:
        self._changed("morale", old, new)

    def watch_military_morale(self, old: float, new: float) -> None:
        self._changed("military_morale", old, new)

    def watch_fallen(self, old: bool, new: bool) -> None:
        self._changed("fallen", old, new, flash=False)

    # --- flashing deltas -----------------------------------------------------

    def _cell_id(self, key: str) -> str:
        return {"treasury": "#sb-treasury", "manpower": "#sb-manpower",
                "morale": "#sb-morale", "military_morale": "#sb-milmorale"}[key]

    def _flash(self, key: str, delta: float) -> None:
        self._flash_deltas[key] = self._flash_deltas.get(key, 0) + delta
        cell = self.query_one(self._cell_id(key), Static)
        total = self._flash_deltas[key]
        cell.set_class(total > 0, "-up")
        cell.set_class(total < 0, "-down")
        if key in self._flash_timers:
            self._flash_timers[key].stop()
        self._flash_timers[key] = self.set_timer(FLASH_SECONDS, lambda: self._clear_flash(key))

    def _clear_flash(self, key: str) -> None:
        self._flash_deltas.pop(key, None)
        self._flash_timers.pop(key, None)
        self.query_one(self._cell_id(key), Static).remove_class("-up", "-down")
        self._render_all()

    def _delta_text(self, key: str, fmt: str) -> Text:
        delta = self._flash_deltas.get(key)
        if not delta:
            return Text()
        arrow = "▲" if delta > 0 else "▼"
        style = f"bold {palette.PHOSPHOR_BRIGHT}" if delta > 0 else f"bold {palette.RED}"
        value = f"{abs(delta):,.0f}" if fmt == "int" else f"{abs(delta):g}"
        return Text(f" {arrow}{value}", style=style)

    # --- rendering -----------------------------------------------------------

    def _morale_cell(self, label: str, key: str, value: float) -> Text:
        style = palette.MORALE_STYLE.get(morale_band(value), palette.PHOSPHOR)
        text = label_value(label, Text(f"{value:.0f}% {palette.meter(value, width=8)}", style=style))
        text.append_text(self._delta_text(key, "points"))
        return text

    def _render_all(self) -> None:
        game = self.app.game

        title = Text()
        if self.fallen:
            title.append(" ⚠ GOVERNMENT FALLEN ", style=f"bold {palette.PHOSPHOR_BRIGHT} on #8b0000")
        else:
            title.append("▣ ", style=palette.AMBER)
            title.append(game.config.get("game_title", "COMMAND TERMINAL"), style=f"bold {palette.PHOSPHOR_BRIGHT}")
            if game.player.bankrupt:
                title.append("  ⚠ STATE BANKRUPT ", style=f"bold {palette.PHOSPHOR_BRIGHT} on #8b4000")
        self.query_one("#sb-title", Static).update(title)

        self.query_one("#sb-date", Static).update(label_value("DATE", f"{game.clock.date_str}  WK {self.turn:03d}"))
        from src.engine.weather_engine import condition, is_freezing, storm_at_sea

        cond = condition(game)
        season = game.weather.get("season", "").upper()
        style = palette.CYAN if is_freezing(game) else (palette.AMBER if cond.get("movement", 1.0) < 0.6 else palette.PHOSPHOR_BRIGHT)
        weather = label_value("WX", Text(f"{cond.get('glyph', '')} {season} · {cond.get('name', 'Clear').split(' — ')[0].upper()}"
                                         + (" · STORMS" if storm_at_sea(game) else ""), style=style))
        self.query_one("#sb-weather", Static).update(weather)

        treasury_style = palette.RED if self.treasury < 0 else palette.PHOSPHOR_BRIGHT
        treasury = label_value("TREASURY", palette.money(self.treasury, game.currency), treasury_style)
        treasury.append_text(self._delta_text("treasury", "int"))
        self.query_one("#sb-treasury", Static).update(treasury)

        manpower = label_value("MANPOWER", f"{self.manpower:,}")
        manpower.append_text(self._delta_text("manpower", "int"))
        self.query_one("#sb-manpower", Static).update(manpower)

        self.query_one("#sb-morale", Static).update(self._morale_cell("CIV", "morale", self.morale))
        self.query_one("#sb-milmorale", Static).update(self._morale_cell("MIL", "military_morale", self.military_morale))
