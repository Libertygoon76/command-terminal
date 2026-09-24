from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.ui import palette
from src.ui.palette import label_value


class EconomyView(VerticalScroll):
    """National ledger and resource stockpiles. Read-only until the economy engine lands (Phase 3)."""

    def compose(self) -> ComposeResult:
        yield Static(id="econ-summary", classes="summary")
        yield Static(Text("RESOURCE STOCKPILES", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="econ-resources", cursor_type="row", zebra_stripes=False)
        yield Static(
            Text("PRODUCTION CHAINS · MARKETS · TRADE ROUTES — MODULE OFFLINE (PHASE 3)", style=palette.PHOSPHOR_DIM),
            classes="module-status",
        )

    def on_mount(self) -> None:
        table = self.query_one("#econ-resources", DataTable)
        table.add_columns("RESOURCE", "CLASS", "STOCKPILE", "UNIT", "BASE PRICE", "BOOK VALUE", "RECIPE")
        self.refresh_view()

    def refresh_view(self) -> None:
        game = self.app.game
        nation = game.player
        cur = game.currency

        summary = Text()
        summary.append_text(label_value("TREASURY    ", palette.money(nation.treasury, cur),
                                        palette.RED if nation.in_debt else palette.PHOSPHOR_BRIGHT))
        summary.append("\n")
        summary.append_text(label_value("TAX RATE    ", f"{nation.tax_rate:.0%}"))
        summary.append("\n")
        summary.append_text(label_value("POPULATION  ", f"{nation.population:,}"))
        summary.append("\n")
        summary.append_text(label_value("WEEKLY NET  ", "— FORECAST UNAVAILABLE —", palette.PHOSPHOR_DIM))
        self.query_one("#econ-summary", Static).update(summary)

        table = self.query_one("#econ-resources", DataTable)
        table.clear()
        for res in game.catalog["resources"]:
            stock = nation.stockpiles.get(res["id"], 0)
            recipe = " + ".join(f"{qty} {rid}" for rid, qty in res.get("recipe", {}).items()) or "—"
            table.add_row(
                res["name"].upper(),
                res["category"].upper(),
                f"{stock:,}",
                res["unit"],
                f"{res['base_price']:,} {cur}",
                f"{stock * res['base_price']:,} {cur}",
                recipe,
            )
