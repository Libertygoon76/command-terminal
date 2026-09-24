"""Economy / War Production: national ledger, military factories, national stockpile, resources.

Select a production line in the table and press + / - to assign or remove a military factory
(0 closes the line). Every change goes through the engine and redraws reactively.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.engine.economy_engine import EconomyError, compute_ledger, shift_tax_policy, tax_policies, tax_policy
from src.engine.production import (
    ProductionError,
    assign_factories,
    demand,
    equipment_by_id,
    forecast,
    is_unlocked,
)
from src.ui import palette
from src.ui.palette import label_value


class EconomyView(VerticalScroll):
    """National ledger, the Military Industrial Complex, and resource stockpiles."""

    BINDINGS = [
        Binding("plus,equals_sign", "assign(1)", "+Factory"),
        Binding("minus", "assign(-1)", "-Factory"),
        Binding("0", "close_line", "Close Line"),
        Binding("left_square_bracket", "tax(-1)", "Tax −"),
        Binding("right_square_bracket", "tax(1)", "Tax +"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="econ-summary", classes="summary")
        yield Static(id="econ-industry-title", classes="section-title")
        yield DataTable(id="econ-production", cursor_type="row", classes="primary-focus")
        yield Static(id="econ-line-detail", classes="module-status")
        yield Static(id="econ-industry-hint", classes="module-status")
        yield Static(Text("RESOURCE STOCKPILES", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="econ-resources", cursor_type="row", zebra_stripes=False)

    def on_mount(self) -> None:
        self.query_one("#econ-production", DataTable).add_columns(
            "PRODUCTION LINE", "STATUS", "FACT.", "SHARE", "EFF.", "OUTPUT/WK", "LAST WK", "STOCKPILE", "FRONT NEEDS")
        self.query_one("#econ-resources", DataTable).add_columns(
            "RESOURCE", "CLASS", "STOCKPILE", "OUTPUT/WK", "UNIT", "BASE PRICE", "BOOK VALUE")
        self.refresh_view()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "econ-production":
            self._render_detail()

    def _render_detail(self) -> None:
        game = self.app.game
        item_id = self._selected_item()
        detail = Text()
        if item_id is not None:
            item = equipment_by_id(game)[item_id]
            detail.append(f"▸ {item['name'].upper()}  ", style=f"bold {palette.PHOSPHOR_BRIGHT}")
            inputs = ", ".join(f"{v:g} {k}" for k, v in item.get("cost", {}).items()) or "none"
            detail.append(f"inputs per unit: {inputs} · {item['batch']:,} {item['unit']} per factory every "
                          f"{item['factory_days']} days", style=palette.PHOSPHOR)
            if not is_unlocked(game.player, item):
                detail.append(f"  ·  REQUIRES RESEARCH: {item['requires_tech']}", style=f"bold {palette.RED}")
        self.query_one("#econ-line-detail", Static).update(detail)

    # --- actions -----------------------------------------------------------------

    def _selected_item(self) -> str | None:
        table = self.query_one("#econ-production", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_assign(self, delta: int) -> None:
        game = self.app.game
        item_id = self._selected_item()
        if item_id is None:
            return
        if game.game_over:
            self.notify("The terminal is locked.", severity="error")
            return
        try:
            count = assign_factories(game, game.player.id, item_id, delta)
        except ProductionError as error:
            self.notify(str(error), title="PRODUCTION", severity="warning")
            return
        self.app.state_changed()
        name = equipment_by_id(game)[item_id]["name"]
        self.notify(f"{name}: {count} factor{'y' if count == 1 else 'ies'} assigned.", title="WAR PRODUCTION")

    def action_tax(self, step: int) -> None:
        game = self.app.game
        try:
            policy = shift_tax_policy(game, game.player.id, step)
        except EconomyError as error:
            self.notify(str(error), title="TAXATION", severity="warning")
            return
        self.app.state_changed()
        drift = float(policy.get("morale_per_week", 0))
        effect = "no effect on morale" if not drift else f"civil morale {'+' if drift > 0 else '−'}{abs(drift):g}/week"
        self.notify(f"{policy['name'].upper()}: {policy['rate']:.0%} tax rate, {effect}.", title="TAX POLICY")

    def action_close_line(self) -> None:
        game = self.app.game
        item_id = self._selected_item()
        if item_id and game.player.production.get(item_id):
            self.action_assign(-game.player.production[item_id])

    # --- rendering ---------------------------------------------------------------

    def refresh_view(self) -> None:
        game = self.app.game
        nation = game.player
        cur = game.currency

        summary = Text()
        summary.append_text(label_value("TREASURY    ", palette.money(nation.treasury, cur),
                                        palette.RED if nation.in_debt else palette.PHOSPHOR_BRIGHT))
        summary.append("\n")
        policy = tax_policy(game, nation)
        ladder = Text()
        for p in tax_policies(game):
            current = p["id"] == nation.tax_policy
            ladder.append(f" {p['name'].upper()} {p['rate']:.0%} ",
                          style=f"bold #000000 on {palette.AMBER}" if current else palette.PHOSPHOR_DIM)
        summary.append_text(label_value("TAX POLICY  ", ladder))
        drift = float(policy.get("morale_per_week", 0))
        drift_style = palette.PHOSPHOR_BRIGHT if drift >= 0 else palette.RED
        summary.append(f"  [ / ] adjust · civil morale {'+' if drift >= 0 else '−'}{abs(drift):g}/wk", style=drift_style)
        summary.append("\n")
        summary.append_text(label_value("POPULATION  ", f"{nation.population:,}"))
        summary.append("\n\n")
        summary.append("WEEKLY LEDGER (PROJECTED)", style=f"bold {palette.AMBER}")
        ledger = compute_ledger(game)
        for label, amount in ledger.income.items():
            summary.append(f"\n  + {amount:>9,} {cur}  ", style=palette.PHOSPHOR_BRIGHT)
            summary.append(label, style=palette.PHOSPHOR_DIM)
        for label, amount in ledger.expenses.items():
            summary.append(f"\n  − {amount:>9,} {cur}  ", style=palette.AMBER)
            summary.append(label, style=palette.PHOSPHOR_DIM)
        net_style = f"bold {palette.PHOSPHOR_BRIGHT}" if ledger.net >= 0 else f"bold {palette.RED}"
        summary.append(f"\n  = {'+' if ledger.net >= 0 else '−'}{abs(ledger.net):>8,} {cur}  NET", style=net_style)
        if nation.bankrupt:
            grace = game.config.get("fail_states", {}).get("bankruptcy_grace_weeks", 8)
            penalties = game.config.get("economy", {}).get("bankruptcy", {})
            summary.append(f"\n\n  ! STATE BANKRUPT — WEEK {game.weeks_insolvent} OF {grace}: civil morale "
                           f"−{penalties.get('civil_morale_per_week', 4)}/wk, military morale "
                           f"−{penalties.get('military_morale_per_week', 6)}/wk, research halted",
                           style=f"bold {palette.RED}")
        self.query_one("#econ-summary", Static).update(summary)

        title = Text()
        title.append("MILITARY INDUSTRY  ", style=f"bold {palette.AMBER}")
        title.append(f"{nation.military_factories} FACTORIES · ", style=palette.PHOSPHOR_BRIGHT)
        title.append(f"{nation.assigned_factories} ASSIGNED · ", style=palette.PHOSPHOR)
        title.append(f"{nation.free_factories} IDLE", style=f"bold {palette.AMBER}" if nation.free_factories else palette.PHOSPHOR_DIM)
        self.query_one("#econ-industry-title", Static).update(title)
        self._render_production()

        report = game.last_production.get(nation.id, {})
        hint = Text()
        hint.append("+ / − assign or remove a factory on the highlighted line · 0 closes the line · "
                    "new lines start at 50% efficiency and improve 10%/week · each assigned factory costs "
                    f"{game.config.get('production', {}).get('upkeep_per_factory', 1100):,} {cur}/wk",
                    style=palette.PHOSPHOR_DIM)
        shortages = report.get("shortages", {})
        if shortages:
            items = equipment_by_id(game)
            hint.append("\n! LAST WEEK SHORT OF RAW MATERIALS: ", style=f"bold {palette.RED}")
            hint.append("; ".join(f"{items[i]['name']} ({', '.join(r)})" for i, r in shortages.items()),
                        style=palette.AMBER)
        self.query_one("#econ-industry-hint", Static).update(hint)

        table = self.query_one("#econ-resources", DataTable)
        table.clear()
        for res in game.catalog["resources"]:
            stock = nation.stockpiles.get(res["id"], 0)
            table.add_row(
                res["name"].upper(),
                res["category"].upper(),
                f"{stock:,}",
                f"+{nation.resource_output.get(res['id'], 0):,}",
                res["unit"],
                f"{res['base_price']:,} {cur}",
                f"{stock * res['base_price']:,} {cur}",
            )

    def _render_production(self) -> None:
        game = self.app.game
        nation = game.player
        table = self.query_one("#econ-production", DataTable)
        cursor_row = table.cursor_row
        table.clear()
        needs = demand(game, nation)
        produced = game.last_production.get(nation.id, {}).get("produced", {})
        total = max(1, nation.military_factories)
        for item_id, item in equipment_by_id(game).items():
            unlocked = is_unlocked(nation, item)
            factories = nation.production.get(item_id, 0)
            status = Text("RUNNING", style=f"bold {palette.PHOSPHOR_BRIGHT}") if factories else (
                Text("IDLE", style=palette.PHOSPHOR_DIM) if unlocked else Text("LOCKED", style=palette.RED))
            gap = max(0, needs.get(item_id, 0) - nation.national_stockpile.get(item_id, 0))
            table.add_row(
                Text(item["name"].upper(), style=palette.PHOSPHOR_BRIGHT if unlocked else palette.PHOSPHOR_DIM),
                status,
                Text(f"{factories}", style=f"bold {palette.AMBER}" if factories else palette.PHOSPHOR_DIM),
                f"{factories / total:.0%}" if factories else "—",
                f"{nation.line_efficiency.get(item_id, 0):.0%}" if factories else "—",
                f"{forecast(game, nation, item_id):,.0f} {item['unit']}" if factories else "—",
                f"{produced.get(item_id, 0):,}" if item_id in produced else "—",
                f"{nation.national_stockpile.get(item_id, 0):,}",
                Text(f"{needs.get(item_id, 0):,}", style=palette.RED if gap else palette.PHOSPHOR),
                key=item_id,
            )
        if table.row_count:
            table.move_cursor(row=min(cursor_row, table.row_count - 1))
        self._render_detail()
