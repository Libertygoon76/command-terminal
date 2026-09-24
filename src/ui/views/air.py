"""Air Assets: abstract air wings assigned to map sectors.

Highlight a wing and press ] / [ to move it to the next / previous sector (the list runs west to east,
with BASE at both ends); 0 recalls it to base. Over a sector where it wins air superiority, every
friendly land battle gets extra firepower, at the price of aviation fuel and bombs from the stockpile.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.engine.air_engine import (
    CONTESTED,
    DENIED,
    GROUNDED,
    SUPERIORITY,
    AirError,
    assign_wing,
    battles_in,
    cycle_wing,
    enemy_air_estimate,
    sector_status,
    sectors,
    wings,
)
from src.engine.weather_engine import air_factor
from src.ui import palette
from src.ui.palette import label_value

STATUS_STYLE = {
    SUPERIORITY: f"bold {palette.PHOSPHOR_BRIGHT}",
    CONTESTED: f"bold {palette.AMBER}",
    DENIED: f"bold {palette.RED}",
    GROUNDED: palette.RED,
    "BASE": palette.PHOSPHOR_DIM,
}


class AirView(VerticalScroll):
    BINDINGS = [
        Binding("right_square_bracket", "cycle(1)", "Next Sector"),
        Binding("left_square_bracket", "cycle(-1)", "Prev Sector"),
        Binding("0", "recall", "Recall to Base"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="air-summary", classes="summary")
        yield Static(Text("AIR WINGS — [ ] move between sectors · 0 recall to base", style=f"bold {palette.AMBER}"),
                     classes="section-title")
        yield DataTable(id="air-wings", cursor_type="row", classes="primary-focus")
        yield Static(id="air-detail", classes="module-status")
        yield Static(Text("SECTORS — the air situation last week", style=f"bold {palette.AMBER}"),
                     classes="section-title")
        yield DataTable(id="air-sectors", cursor_type="row")

    def on_mount(self) -> None:
        self.query_one("#air-wings", DataTable).add_columns(
            "WING", "AIRCRAFT", "SECTOR", "STATUS", "SUPPORT SORTIES", "LOSSES LAST WK")
        self.query_one("#air-sectors", DataTable).add_columns(
            "SECTOR", "CONTROL", "OUR WINGS", "OUR AIRCRAFT", "ENEMY AIR (EST.)", "SITUATION", "OUR BATTLES")
        self.refresh_view()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    def _selected(self) -> str | None:
        table = self.query_one("#air-wings", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    # --- actions ---------------------------------------------------------------

    def action_cycle(self, step: int) -> None:
        wing_id = self._selected()
        if wing_id is None:
            return
        try:
            wing = cycle_wing(self.app.game, wing_id, step)
        except AirError as error:
            self.notify(str(error), title="AIR", severity="warning")
            return
        self.app.state_changed()
        where = self.app.game.world_map.regions[wing.sector].name.upper() if wing.sector else "BASE"
        self.notify(f"{wing.name} → {where}", title="AIR TASKING")

    def action_recall(self) -> None:
        wing_id = self._selected()
        if wing_id is None:
            return
        try:
            wing = assign_wing(self.app.game, wing_id, None)
        except AirError as error:
            self.notify(str(error), title="AIR", severity="warning")
            return
        self.app.state_changed()
        self.notify(f"{wing.name} recalled to base.", title="AIR TASKING")

    # --- rendering -------------------------------------------------------------

    def refresh_view(self) -> None:
        game = self.app.game
        nation = game.player
        cfg = game.config.get("air", {})
        world = game.world_map
        ours = wings(game, nation.id)
        stock = nation.national_stockpile

        summary = Text()
        summary.append_text(label_value("AIR WINGS         ", f"{len(ours)}  ({sum(w.aircraft for w in ours)} aircraft)"))
        summary.append("\n")
        summary.append_text(label_value("IN RESERVE        ", f"{stock.get('strike_aircraft', 0):,} strike fighters in depots"))
        summary.append("\n")
        summary.append_text(label_value("AVIATION FUEL     ", f"{stock.get('aviation_fuel', 0):,} drums",
                                        palette.PHOSPHOR_BRIGHT if stock.get("aviation_fuel", 0) else f"bold {palette.RED}"))
        summary.append("\n")
        summary.append_text(label_value("BOMBS             ", f"{stock.get('bombs', 0):,}",
                                        palette.PHOSPHOR_BRIGHT if stock.get("bombs", 0) else f"bold {palette.RED}"))
        summary.append("\n")
        flying = air_factor(game)
        summary.append_text(label_value("FLYING WEATHER    ", f"{flying:.0%} of sorties can fly" if flying else "ALL WINGS GROUNDED",
                                        palette.PHOSPHOR_BRIGHT if flying >= 0.9 else palette.AMBER if flying else f"bold {palette.RED}"))
        self.query_one("#air-summary", Static).update(summary)

        table = self.query_one("#air-wings", DataTable)
        cursor = table.cursor_row
        table.clear()
        for w in ours:
            sector = world.regions[w.sector].name.upper() if w.sector else "— BASE —"
            table.add_row(
                Text(w.name.upper(), style=palette.PHOSPHOR_BRIGHT),
                f"{w.aircraft} / {w.establishment}",
                sector,
                Text(w.status, style=STATUS_STYLE.get(w.status, palette.PHOSPHOR)),
                f"{w.sorties}",
                Text(f"{w.losses}", style=palette.RED if w.losses else palette.PHOSPHOR),
                key=w.id,
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

        detail = Text()
        detail.append("▸ Superiority over a sector: our land battles there fight with ", style=palette.PHOSPHOR_DIM)
        detail.append(f"+{cfg.get('attack_bonus', 0.3):.0%} firepower", style=palette.PHOSPHOR_BRIGHT)
        detail.append(f" (+{cfg.get('attack_bonus_no_bombs', 0.1):.0%} without bombs). Each aircraft burns "
                      f"{cfg.get('patrol_fuel_per_aircraft', 1.5):g} drums of aviation fuel a week on patrol, and "
                      f"{cfg.get('support_fuel_per_aircraft', 2.5):g} more plus {cfg.get('bombs_per_aircraft', 5):g} bombs "
                      "over a battle. Superiority needs 1.5× the enemy's air power in the sector.",
                      style=palette.PHOSPHOR_DIM)
        self.query_one("#air-detail", Static).update(detail)

        sectors_table = self.query_one("#air-sectors", DataTable)
        cursor = sectors_table.cursor_row
        sectors_table.clear()
        owner_style = {"kestria": palette.PHOSPHOR_BRIGHT, "vosk": palette.RED}
        for sector in sectors(game):
            region = world.regions[sector]
            here = [w for w in ours if w.sector == sector]
            status = sector_status(game, sector, nation.id)
            owner = game.nations[region.owner].adjective.upper() if region.owner in game.nations else region.owner.upper()
            fighting = battles_in(game, sector, nation.id)
            sectors_table.add_row(
                region.name.upper(),
                Text(owner, style=owner_style.get(region.owner, palette.AMBER)),
                ", ".join(w.name.split(" ")[0] for w in here) or "—",
                f"{sum(w.aircraft for w in here)}" if here else "—",
                enemy_air_estimate(game, sector),
                Text(status, style=STATUS_STYLE.get(status, palette.PHOSPHOR_DIM)),
                Text("IN BATTLE", style=f"bold {palette.RED}") if fighting else "—",
                key=sector,
            )
        if sectors_table.row_count:
            sectors_table.move_cursor(row=min(cursor, sectors_table.row_count - 1))
