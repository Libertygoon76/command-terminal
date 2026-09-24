"""Research & Development: choose one technology to research at a time."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.engine.research import ResearchError, start_research, status, stop_research, techs, weeks_remaining
from src.ui import palette

STATUS_STYLE = {
    "KNOWN": palette.PHOSPHOR_DIM,
    "ACTIVE": f"bold {palette.AMBER}",
    "AVAILABLE": palette.PHOSPHOR_BRIGHT,
    "LOCKED": palette.RED,
}


class ResearchView(VerticalScroll):
    """Tech tree table + detail. ENTER starts the highlighted project, X stops the active one."""

    BINDINGS = [
        Binding("r", "start", "Research"),
        Binding("x", "stop", "Stop Research"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="rnd-summary", classes="summary")
        yield Static(Text("TECHNOLOGY", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="rnd-table", cursor_type="row", classes="primary-focus")
        yield Static(id="rnd-detail", classes="module-status")

    def on_mount(self) -> None:
        self.query_one("#rnd-table", DataTable).add_columns(
            "TECHNOLOGY", "BRANCH", "STATUS", "PROGRESS", "WEEKS LEFT", "COST/WK", "UNLOCKS")
        self.refresh_view()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._render_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()  # ENTER on the table
        self.action_start()

    def _selected(self) -> str | None:
        table = self.query_one("#rnd-table", DataTable)
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    # --- actions ---------------------------------------------------------------

    def action_start(self) -> None:
        tech_id = self._selected()
        if tech_id is None:
            return
        try:
            tech = start_research(self.app.game, self.app.game.player.id, tech_id)
        except ResearchError as error:
            self.notify(str(error), title="R&D", severity="warning")
            return
        self.app.state_changed()
        left = weeks_remaining(self.app.game, self.app.game.player, tech_id)
        self.notify(f"{tech['name']}: {left:.0f} weeks at {tech['weekly_cost']:,} {self.app.game.currency}/wk.",
                    title="RESEARCH STARTED")

    def action_stop(self) -> None:
        nation = self.app.game.player
        if nation.research_project is None:
            self.notify("No project is running.", severity="warning")
            return
        name = techs(self.app.game)[nation.research_project]["name"]
        stop_research(self.app.game, nation.id)
        self.app.state_changed()
        self.notify(f"{name} shelved. Progress is kept.", title="RESEARCH STOPPED")

    # --- rendering -------------------------------------------------------------

    def refresh_view(self) -> None:
        game = self.app.game
        nation = game.player
        table_data = techs(game)
        summary = Text()
        summary.append("ACTIVE PROJECT   ", style=palette.PHOSPHOR_DIM)
        if nation.research_project:
            tech = table_data[nation.research_project]
            done = nation.research_progress.get(tech["id"], 0.0)
            summary.append(f"{tech['name'].upper()}\n", style=f"bold {palette.AMBER}")
            summary.append("PROGRESS         ", style=palette.PHOSPHOR_DIM)
            summary.append(f"{palette.meter(100 * done / max(1, tech['weeks']), width=20)}  "
                           f"{done:.0f} / {tech['weeks']} WEEKS\n", style=palette.PHOSPHOR_BRIGHT)
            summary.append("COST             ", style=palette.PHOSPHOR_DIM)
            summary.append(f"{tech['weekly_cost']:,} {game.currency} / WEEK", style=palette.AMBER)
            if nation.bankrupt:
                summary.append("\n! LABORATORIES UNPAID — RESEARCH HALTED WHILE THE STATE IS BANKRUPT",
                               style=f"bold {palette.RED}")
        else:
            summary.append("NONE — LABORATORIES IDLE\n", style=f"bold {palette.RED}")
            summary.append("Highlight an AVAILABLE technology and press ENTER.", style=palette.PHOSPHOR_DIM)
        known = sum(1 for t in table_data if t in nation.known_techs)
        summary.append(f"\nKNOWN            {known} OF {len(table_data)} TECHNOLOGIES", style=palette.PHOSPHOR_DIM)
        self.query_one("#rnd-summary", Static).update(summary)

        items = {e["id"]: e for e in game.catalog["equipment"]}
        table = self.query_one("#rnd-table", DataTable)
        cursor = table.cursor_row
        table.clear()
        for tech_id, tech in table_data.items():
            state = status(game, nation, tech_id)
            done = nation.research_progress.get(tech_id, 0.0)
            weeks = max(1, tech["weeks"])
            progress = "COMPLETE" if state == "KNOWN" else f"{palette.meter(100 * done / weeks, width=10)} {done / weeks:.0%}"
            unlocks = ", ".join(items[i]["name"] for i in tech.get("unlocks", []) if i in items) or \
                ", ".join(f"{k.replace('_', ' ')}" for k in tech.get("effects", {})) or "—"
            table.add_row(
                Text(tech["name"].upper(), style=STATUS_STYLE[state] if state != "AVAILABLE" else palette.PHOSPHOR_BRIGHT),
                tech.get("branch", "").replace("_", " ").upper(),
                Text(state, style=STATUS_STYLE[state]),
                progress,
                "—" if state == "KNOWN" else f"{weeks_remaining(game, nation, tech_id):.0f}",
                "—" if state == "KNOWN" else f"{tech.get('weekly_cost', 0):,}",
                unlocks[:48],
                key=tech_id,
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
        self._render_detail()

    def _render_detail(self) -> None:
        game = self.app.game
        tech_id = self._selected()
        detail = Text()
        if tech_id is not None:
            tech = techs(game)[tech_id]
            state = status(game, game.player, tech_id)
            detail.append(f"▸ {tech['name'].upper()}  ", style=f"bold {palette.PHOSPHOR_BRIGHT}")
            requires = [techs(game)[r]["name"] for r in tech.get("requires", [])]
            detail.append(f"requires: {', '.join(requires) or 'nothing'}", style=palette.PHOSPHOR)
            if state == "LOCKED":
                detail.append("  (NOT YET AVAILABLE)", style=palette.RED)
            upgraded = [t["name"] for t in game.catalog["units"]["units"] if tech_id in t.get("upgrades", {})]
            if upgraded:
                detail.append(f"\n  new kit for: {', '.join(upgraded)}", style=palette.PHOSPHOR_DIM)
            if tech.get("notes"):
                detail.append(f"\n  {tech['notes']}", style=palette.PHOSPHOR_DIM)
        detail.append("\nENTER start / switch project (progress is kept) · X stop · one project at a time",
                      style=palette.PHOSPHOR_DIM)
        self.query_one("#rnd-detail", Static).update(detail)
