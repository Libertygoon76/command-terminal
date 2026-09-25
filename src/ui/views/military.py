"""Military: force overview, Recruitment & Training, formations in the field, combat stances.

Highlight a formation type in the RECRUITMENT table and press R to raise one (treasury and
manpower are paid up front). Highlight a formation IN TRAINING and press X to cancel it.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.engine.court import regency_price
from src.engine.logistics_engine import fill_ratio
from src.engine.recruitment import RecruitmentError, cancel_training, muster_point, raise_formation
from src.ui import palette
from src.ui.palette import label_value


def readiness(game, unit) -> float:
    """Combat readiness: the worse of weapons and ammunition fill."""
    weapons = fill_ratio(game, unit, ("small_arms", "artillery", "armor"))
    return min(weapons, fill_ratio(game, unit))


class MilitaryView(VerticalScroll):
    BINDINGS = [
        Binding("r", "raise_unit", "Raise Formation"),
        Binding("x", "cancel_training", "Cancel Training"),
        Binding("f", "relieve", "Relieve Commander"),
        Binding("k", "royal_command", "Royal General"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="mil-summary", classes="summary")
        yield Static(Text("RECRUITMENT — raise a new formation (R)", style=f"bold {palette.AMBER}"),
                     classes="section-title")
        yield DataTable(id="mil-recruit", cursor_type="row", classes="primary-focus")
        yield Static(id="mil-training-title", classes="section-title")
        yield DataTable(id="mil-training", cursor_type="row")
        yield Static(Text("FORMATIONS IN THE FIELD — highlight one and press F to RELIEVE its commander "
                          "(costs military morale)", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="mil-formations", cursor_type="row")
        yield Static(Text("COMBAT STANCES (set per formation in the War Room: T)", style=f"bold {palette.AMBER}"),
                     classes="section-title")
        yield Static(id="mil-stances")

    def on_mount(self) -> None:
        self.query_one("#mil-recruit", DataTable).add_columns(
            "FORMATION TYPE", "MEN", "COST", "TRAINING", "FUEL/WK (MOVING)", "SPEED", "RECON", "CAN RAISE")
        self.query_one("#mil-training", DataTable).add_columns(
            "ID", "FORMATION", "TYPE", "PROGRESS", "WEEKS LEFT", "MUSTERS AT", "COST PAID")
        self.query_one("#mil-formations", DataTable).add_columns(
            "ID", "FORMATION", "TYPE", "STRENGTH", "MORALE", "SUPPLY", "AMMO", "READY", "LINE", "STATUS", "STANCE/MISSION", "COMMANDER",
            "ORDER", "GRID")
        stances = Text()
        for stance in self.app.game.catalog["units"].get("stances", []):
            stances.append(f"  ▸ {stance['name'].upper():<12}", style=palette.PHOSPHOR_BRIGHT)
            stances.append(stance["description"] + "\n", style=palette.PHOSPHOR)
        self.query_one("#mil-stances", Static).update(stances)
        self.refresh_view()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    # --- actions ---------------------------------------------------------------

    @staticmethod
    def _key(table: DataTable) -> str | None:
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def action_raise_unit(self) -> None:
        game = self.app.game
        template_id = self._key(self.query_one("#mil-recruit", DataTable))
        if template_id is None:
            return
        try:
            order = raise_formation(game, game.player.id, template_id)
        except RecruitmentError as error:
            self.notify(str(error), title="RECRUITMENT", severity="warning")
            return
        self.app.state_changed()
        self.notify(f"{order.name} ({order.designation}) will muster in {order.weeks_total} weeks.",
                    title="FORMATION RAISED")

    def action_relieve(self) -> None:
        from src.engine.command import CommandError, relieve_commander

        game = self.app.game
        unit_id = self._key(self.query_one("#mil-formations", DataTable))
        if unit_id is None:
            self.notify("Highlight a formation in the field first.", severity="warning")
            return
        try:
            old, new = relieve_commander(game, unit_id)
        except CommandError as error:
            self.notify(str(error), title="CHAIN OF COMMAND", severity="warning")
            return
        self.app.state_changed()
        cost = game.catalog.get("commanders", {}).get("relieve", {}).get("military_morale", -6)
        self.notify(f"{old} relieved. {new} takes command. Military morale {cost:+g}.", title="CHANGE OF COMMAND",
                    severity="warning")

    def action_royal_command(self) -> None:
        """K: give the highlighted division to the most martial member of the House, or recall the royal."""
        from src.engine import court

        game = self.app.game
        unit_id = self._key(self.query_one("#mil-formations", DataTable))
        unit = game.unit(unit_id) if unit_id else None
        if unit is None:
            self.notify("Highlight a formation in the field first.", severity="warning")
            return
        try:
            royal = court.royal_of(game, unit)
            if royal is not None:
                court.recall_commander(game, unit.id)
                message = f"{royal.name} recalled to court. {unit.commander} takes command."
            else:
                candidates = court.eligible_generals(game)
                if not candidates:
                    raise court.CourtError("No member of the House is at court and free to command.")
                court.appoint_commander(game, candidates[0].id, unit.id)
                message = (f"{candidates[0].name} (MIL {candidates[0].military}) takes command of the {unit.name}. "
                           "Royals never disobey — but if killed in action, the House loses 20 stability.")
        except (court.CourtError, court.GameOverError) as error:
            self.notify(str(error), title="ROYAL GENERAL", severity="warning")
            return
        self.app.state_changed()
        self.notify(message, title="ROYAL GENERAL")

    def action_cancel_training(self) -> None:
        game = self.app.game
        order_id = self._key(self.query_one("#mil-training", DataTable))
        if order_id is None:
            self.notify("No formation in training is selected.", severity="warning")
            return
        try:
            order = cancel_training(game, order_id)
        except RecruitmentError as error:
            self.notify(str(error), title="RECRUITMENT", severity="warning")
            return
        self.app.state_changed()
        self.notify(f"{order.name} disbanded. Manpower returned; half the cost refunded.", title="TRAINING CANCELLED")

    # --- rendering -------------------------------------------------------------

    def refresh_view(self) -> None:
        game = self.app.game
        nation = game.player
        cur = game.currency
        units = nation.units
        training = [o for o in game.training if o.nation_id == nation.id]

        summary = Text()
        summary.append_text(label_value("MANPOWER POOL     ", f"{nation.manpower:,}"))
        summary.append("\n")
        coup_at = game.config.get("fail_states", {}).get("coup_military_morale", 10)
        mil_style = palette.MORALE_STYLE.get(nation.military_morale_band, palette.PHOSPHOR)
        summary.append_text(label_value("MILITARY MORALE   ", Text(
            f"{nation.military_morale:.0f} / 100  {palette.meter(nation.military_morale)}  "
            f"{nation.military_morale_band}  (COUP AT {coup_at})", style=mil_style)))
        summary.append("\n")
        summary.append_text(label_value("ACTIVE FORMATIONS ", f"{len(units)}  ({nation.total_deployed:,} MEN DEPLOYED)"))
        summary.append("\n")
        summary.append_text(label_value("IN TRAINING       ", f"{len(training)}"))
        summary.append("\n")
        low_supply = sum(1 for u in units if u.supply < 75)
        cut = sum(1 for u in units if u.supply_state != "supplied")
        supply_text = f"{low_supply} FORMATION(S) BELOW 75%" if low_supply else "ALL FORMATIONS ADEQUATELY SUPPLIED"
        if cut:
            supply_text += f" · {cut} OUT OF SUPPLY RANGE"
        summary.append_text(label_value("SUPPLY STATUS     ", supply_text,
                                        palette.AMBER if low_supply else palette.PHOSPHOR_BRIGHT))
        self.query_one("#mil-summary", Static).update(summary)

        # Recruitment options.
        recruit = self.query_one("#mil-recruit", DataTable)
        cursor = recruit.cursor_row
        recruit.clear()
        for template in game.catalog["units"]["units"]:
            cost = regency_price(game, nation, int(template["recruit_cost"]))
            affordable = nation.treasury >= cost and nation.manpower >= template["manpower"]
            recruit.add_row(
                Text(f"[{template['symbol']}] {template['name'].upper()}", style=palette.PHOSPHOR_BRIGHT),
                f"{template['manpower']:,}",
                f"{cost:,} {cur}",
                f"{template['training_weeks']} WK",
                f"{template.get('fuel_use', {}).get('moving', 0):,} drums",
                f"{template.get('speed_mpd', 0):g}{'+' + format(template['truck_mpd'], 'g') if template.get('truck_mpd') else ''} mi/day",
                f"{template.get('detection_radius', 0)} rows",
                Text("YES" if affordable else "NO", style=palette.PHOSPHOR_BRIGHT if affordable else palette.RED),
                key=template["id"],
            )
        if recruit.row_count:
            recruit.move_cursor(row=min(cursor, recruit.row_count - 1))

        # Training queue.
        title = Text()
        title.append("IN TRAINING — cancel with X ", style=f"bold {palette.AMBER}")
        x, y = muster_point(game, nation)
        title.append(f"(new formations muster at grid {x:03d}-{y:03d}, warships at the busiest open harbour, with ¼ of "
                     "their kit; logistics issues the rest from the national stockpile)", style=palette.PHOSPHOR_DIM)
        self.query_one("#mil-training-title", Static).update(title)
        queue = self.query_one("#mil-training", DataTable)
        cursor = queue.cursor_row
        queue.clear()
        names = {t["id"]: t["name"] for t in game.catalog["units"]["units"]}
        templates_by_id = {t["id"]: t for t in game.catalog["units"]["units"]}
        for order in training:
            queue.add_row(
                order.designation,
                order.name.upper(),
                names[order.unit_type].upper(),
                f"{palette.meter(order.progress * 100, width=12)} {order.progress:.0%}",
                f"{order.weeks_left}",
                "{:03d}-{:03d}".format(*muster_point(game, nation, templates_by_id[order.unit_type])),
                f"{order.cost:,} {cur}",
                key=order.id,
            )
        if queue.row_count:
            queue.move_cursor(row=min(cursor, queue.row_count - 1))

        # Formations in the field.
        templates = {t["id"]: t for t in game.catalog["units"]["units"]}
        from src.engine.electronic_warfare import is_dark
        from src.ui.views.map import commander_text

        table = self.query_one("#mil-formations", DataTable)
        cursor = table.cursor_row
        table.clear()
        for unit in sorted(units, key=lambda u: u.designation):
            template = templates[unit.unit_type]
            if is_dark(game, unit):
                blank = Text("NO SIGNAL", style=f"bold {palette.RED}")
                table.add_row(unit.designation, unit.name.upper(), Text(f"[{template['symbol']}] {template['name'].upper()}"),
                              blank, "—", "—", "—", "—", "—", Text("CONTACT LOST", style=f"bold {palette.RED}"), "—",
                              commander_text(game, unit), "—", "—", key=unit.id)
                continue
            ammo = fill_ratio(game, unit)
            ready = readiness(game, unit)
            table.add_row(
                unit.designation,
                unit.name.upper(),
                Text(f"[{template['symbol']}] {template['name'].upper()}"),
                f"{unit.strength:,}",
                f"{unit.morale:.0f}%",
                Text(f"{unit.supply:.0f}%", style=palette.PHOSPHOR_BRIGHT if unit.supply >= 75 else palette.AMBER),
                Text(f"{ammo:.0%}", style=palette.PHOSPHOR_BRIGHT if ammo >= 0.5 else f"bold {palette.RED}"),
                Text(f"{ready:.0%}", style=palette.PHOSPHOR_BRIGHT if ready >= 0.75 else palette.AMBER),
                Text(unit.supply_state.upper(),
                     style=palette.PHOSPHOR if unit.supply_state == "supplied" else f"bold {palette.RED}"),
                Text(("RELIEF DUTY" if unit.relief_weeks else unit.status.upper())
                     + (" [INFECTED]" if f"unit:{unit.id}" in game.infections else ""),
                     style=f"bold {palette.RED}" if unit.engaged or f"unit:{unit.id}" in game.infections else palette.PHOSPHOR),
                unit.mission.upper() if template.get("domain") == "sea" else unit.stance.upper(),
                commander_text(game, unit),
                (f"MOVE → {unit.active_order.x:03d}-{unit.active_order.y:03d}" if unit.active_order else "HOLD"),
                f"{unit.x:03d}-{unit.y:03d}",
                key=unit.id,
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
