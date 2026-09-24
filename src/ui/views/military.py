from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.engine.logistics_engine import fill_ratio
from src.ui import palette
from src.ui.palette import label_value


class MilitaryView(VerticalScroll):
    """Force overview and unit catalog. Recruitment/training arrives in Phase 4."""

    def compose(self) -> ComposeResult:
        yield Static(id="mil-summary", classes="summary")
        yield Static(Text("FORMATIONS IN THE FIELD", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="mil-formations", cursor_type="row")
        yield Static(Text("UNIT TEMPLATES", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="mil-units", cursor_type="row")
        yield Static(Text("COMBAT STANCES (set per formation in the War Room: T)", style=f"bold {palette.AMBER}"),
                     classes="section-title")
        yield Static(id="mil-stances")
        yield Static(
            Text("RECRUITMENT · TRAINING · PRODUCTION — MODULE OFFLINE (FUTURE PHASES)", style=palette.PHOSPHOR_DIM),
            classes="module-status",
        )

    def on_mount(self) -> None:
        self.query_one("#mil-formations", DataTable).add_columns(
            "ID", "FORMATION", "TYPE", "STRENGTH", "MORALE", "SUPPLY", "AMMO", "LINE", "STATUS", "STANCE", "ORDER", "GRID"
        )
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
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    def refresh_view(self) -> None:
        nation = self.app.game.player
        summary = Text()
        summary.append_text(label_value("MANPOWER POOL     ", f"{nation.manpower:,}"))
        summary.append("\n")
        coup_at = self.app.game.config.get("fail_states", {}).get("coup_military_morale", 10)
        mil_style = palette.MORALE_STYLE.get(nation.military_morale_band, palette.PHOSPHOR)
        summary.append_text(label_value("MILITARY MORALE   ", Text(
            f"{nation.military_morale:.0f} / 100  {palette.meter(nation.military_morale)}  "
            f"{nation.military_morale_band}  (COUP AT {coup_at})", style=mil_style)))
        summary.append("\n")
        units = nation.units
        summary.append_text(label_value("ACTIVE FORMATIONS ", f"{len(units)}  ({nation.total_deployed:,} MEN DEPLOYED)"))
        summary.append("\n")
        summary.append_text(label_value("IN TRAINING       ", "0"))
        summary.append("\n")
        low_supply = sum(1 for u in units if u.supply < 75)
        cut = sum(1 for u in units if u.supply_state != "supplied")
        supply_text = f"{low_supply} FORMATION(S) BELOW 75%" if low_supply else "ALL FORMATIONS ADEQUATELY SUPPLIED"
        if cut:
            supply_text += f" · {cut} OUT OF SUPPLY RANGE"
        summary.append_text(label_value("SUPPLY STATUS     ", supply_text,
                                        palette.AMBER if low_supply else palette.PHOSPHOR_BRIGHT))
        self.query_one("#mil-summary", Static).update(summary)

        game = self.app.game
        templates = {t["id"]: t for t in game.catalog["units"]["units"]}
        table = self.query_one("#mil-formations", DataTable)
        table.clear()
        for unit in sorted(units, key=lambda u: u.designation):
            template = templates[unit.unit_type]
            table.add_row(
                unit.designation,
                unit.name.upper(),
                Text(f"[{template['symbol']}] {template['name'].upper()}"),
                f"{unit.strength:,}",
                f"{unit.morale:.0f}%",
                Text(f"{unit.supply:.0f}%", style=palette.PHOSPHOR_BRIGHT if unit.supply >= 75 else palette.AMBER),
                Text(f"{fill_ratio(game, unit):.0%}",
                     style=palette.PHOSPHOR_BRIGHT if fill_ratio(game, unit) >= 0.5 else f"bold {palette.RED}"),
                Text(unit.supply_state.upper(),
                     style=palette.PHOSPHOR if unit.supply_state == "supplied" else f"bold {palette.RED}"),
                Text(unit.status.upper(), style=f"bold {palette.RED}" if unit.engaged else palette.PHOSPHOR),
                unit.stance.upper(),
                (f"MOVE → {unit.active_order.x:03d}-{unit.active_order.y:03d}" if unit.active_order else "HOLD"),
                f"{unit.x:03d}-{unit.y:03d}",
            )
