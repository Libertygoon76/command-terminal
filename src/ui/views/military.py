from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.ui import palette
from src.ui.palette import label_value


class MilitaryView(VerticalScroll):
    """Force overview and unit catalog. Recruitment/training arrives in Phase 4."""

    def compose(self) -> ComposeResult:
        yield Static(id="mil-summary", classes="summary")
        yield Static(Text("UNIT TEMPLATES", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="mil-units", cursor_type="row")
        yield Static(Text("DOCTRINE — TACTICAL STANCES", style=f"bold {palette.AMBER}"), classes="section-title")
        yield Static(id="mil-stances")
        yield Static(
            Text("RECRUITMENT · TRAINING · SUPPLY LINES — MODULE OFFLINE (PHASE 4-5)", style=palette.PHOSPHOR_DIM),
            classes="module-status",
        )

    def on_mount(self) -> None:
        table = self.query_one("#mil-units", DataTable)
        table.add_columns("FORMATION", "MEN", "COST", "TRAIN", "RATIONS/WK", "FUEL/WK", "AMMO/WK", "ATK", "DEF", "BRK")
        cur = self.app.game.currency
        units = self.app.game.catalog["units"]
        for unit in units["units"]:
            upkeep = unit["upkeep"]
            combat = unit["combat"]
            table.add_row(
                unit["name"].upper(),
                f"{unit['manpower']:,}",
                f"{unit['recruit_cost']:,} {cur}",
                f"{unit['training_weeks']} WK",
                f"{upkeep.get('rations', 0):,}",
                f"{upkeep.get('fuel', 0):,}",
                f"{upkeep.get('munitions', 0):,}",
                str(combat["attack"]),
                str(combat["defense"]),
                str(combat["breakthrough"]),
            )

        stances = Text()
        for stance in units.get("stances", []):
            stances.append(f"  ▸ {stance['name'].upper():<20}", style=palette.PHOSPHOR_BRIGHT)
            stances.append(stance["description"] + "\n", style=palette.PHOSPHOR)
        self.query_one("#mil-stances", Static).update(stances)
        self.refresh_view()

    def refresh_view(self) -> None:
        nation = self.app.game.player
        summary = Text()
        summary.append_text(label_value("MANPOWER POOL     ", f"{nation.manpower:,}"))
        summary.append("\n")
        summary.append_text(label_value("ACTIVE FORMATIONS ", "0"))
        summary.append("\n")
        summary.append_text(label_value("IN TRAINING       ", "0"))
        summary.append("\n")
        summary.append_text(label_value("SUPPLY STATUS     ", "NO FORMATIONS DEPLOYED", palette.PHOSPHOR_DIM))
        self.query_one("#mil-summary", Static).update(summary)
