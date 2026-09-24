"""[0] ROYAL COURT: the ruling House, the cabinet, audiences, marriages and the line of succession.

Highlight a courtier: their dossier appears below. A holds court (a courtier is granted an audience); F / W / I
appoint the highlighted courtier Minister of Finance / Minister of War / Head of Intelligence; D dismisses them from
office; M proposes a political marriage abroad. Royal generals are appointed from the Military screen (K).
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Static

from src.engine import court
from src.models.character import OFFICES, RULER
from src.ui import palette

OFFICE_KEYS = {"finance": "F", "war": "W", "intelligence": "I"}
OFFICE_SHORT = {"finance": "FINANCE", "war": "WAR", "intelligence": "INTELLIGENCE"}


def loyalty_style(value: float) -> str:
    if value < 20:
        return f"bold {palette.RED}"
    if value < 40:
        return palette.AMBER
    return palette.PHOSPHOR_BRIGHT


class CourtView(VerticalScroll):
    BINDINGS = [
        Binding("a", "hold_court", "Hold Court"),
        Binding("f", "appoint('finance')", "Finance"),
        Binding("w", "appoint('war')", "War"),
        Binding("i", "appoint('intelligence')", "Intelligence"),
        Binding("d", "dismiss", "Dismiss"),
        Binding("m", "marriage", "Marriage"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.char_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="court-summary", classes="summary")
        yield Static(id="court-cabinet")
        yield Static(Text("THE COURT — A hold court · F/W/I appoint to Finance/War/Intelligence · D dismiss · "
                          "M marriage abroad · royal generals: Military screen, K", style=f"bold {palette.AMBER}"),
                     classes="section-title")
        yield DataTable(id="court-table", cursor_type="row", classes="primary-focus")
        with Horizontal(id="court-panels"):
            yield Static(id="court-detail")
            with VerticalScroll(id="court-history-scroll"):
                yield Static(id="court-history")

    def on_mount(self) -> None:
        self.query_one("#court-table", DataTable).add_columns(
            "NAME", "AGE", "RELATION", "POST", "ADM", "MIL", "INT", "INFL", "TRAITS", "LOYALTY", "STATUS")
        self.query_one("#court-cabinet").border_title = "THE CABINET"
        self.query_one("#court-history-scroll").border_title = "CHRONICLE OF THE HOUSE"
        self.refresh_view()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None:
            self.char_id = event.row_key.value
            self._render_detail()

    # --- actions ---------------------------------------------------------------

    def _act(self, fn, title: str):
        try:
            result = fn()
        except (court.CourtError, court.GameOverError) as error:
            self.notify(str(error), title=title, severity="warning")
            return None
        self.app.state_changed()
        return result

    def action_hold_court(self) -> None:
        card = self._act(lambda: court.hold_court(self.app.game), "ROYAL COURT")
        if card:
            self.screen.show_dilemma()

    def action_appoint(self, office: str) -> None:
        if not self.char_id:
            return
        game = self.app.game
        previous = self._act(lambda: court.appoint(game, self.char_id, office), "THE CABINET")
        char = game.player.dynasty.characters[self.char_id]
        if char.office == office:
            note = f" {previous.name} is dismissed and resents it." if previous else ""
            self.notify(f"{char.name} is the new {court.office_name(game, office)}.{note}", title="THE CABINET")

    def action_dismiss(self) -> None:
        if not self.char_id:
            return
        office = self._act(lambda: court.dismiss(self.app.game, self.char_id), "THE CABINET")
        if office:
            self.notify(f"{court.office_name(self.app.game, office)} is vacant.", title="THE CABINET",
                        severity="warning")

    def action_marriage(self) -> None:
        if not self.char_id:
            return
        card = self._act(lambda: court.marriage_card(self.app.game, self.char_id), "A POLITICAL MARRIAGE")
        if card:
            self.screen.show_dilemma()

    # --- rendering -------------------------------------------------------------

    def refresh_view(self) -> None:
        game = self.app.game
        house = game.player.dynasty
        if house is None:
            self.query_one("#court-summary", Static).update(Text("NO RULING HOUSE.", style=palette.PHOSPHOR_DIM))
            return
        ruler = house.ruler
        heir = next((c for c in house.living() if c.relation == "Heir"), None)
        summary = Text()
        summary.append(f"{house.name.upper()}   ", style=f"bold {palette.PHOSPHOR_BRIGHT}")
        summary.append(f"LORD PROTECTOR {ruler.name.upper()} ({ruler.age})   ", style=palette.PHOSPHOR)
        summary.append(f"HEIR {heir.name.upper() if heir else 'NONE'}   ",
                       style=palette.PHOSPHOR if heir else f"bold {palette.RED}")
        stab_style = f"bold {palette.RED}" if house.stability < 25 else palette.PHOSPHOR
        summary.append(f"STABILITY {house.stability:.0f} {palette.meter(house.stability, width=10)}   ", style=stab_style)
        plotting = sum(1 for c in house.living() if c.relation != RULER and not c.imprisoned
                       and c.loyalty < float(court.cfg(game).get("treason", {}).get("threshold", 20)))
        summary.append(f"DISLOYAL {plotting}   ", style=f"bold {palette.RED}" if plotting else palette.PHOSPHOR_DIM)
        cooldown = int(court.cfg(game).get("audiences", {}).get("hold_court_cooldown", 2))
        ready = game.clock.turn - house.last_court_turn >= cooldown
        summary.append("AUDIENCE CHAMBER OPEN [A]" if ready else
                       f"NEXT AUDIENCE DAY WK {house.last_court_turn + cooldown:03d}",
                       style=palette.AMBER if ready else palette.PHOSPHOR_DIM)
        self.query_one("#court-summary", Static).update(summary)

        cabinet = Text()
        spec = court.cfg(game).get("cabinet", {})
        for office in OFFICES:
            holder = house.minister(office)
            stat = court.office_stat(game, office)
            delta = court.office_delta(game, office)
            if office == "finance":
                effect = f"tax revenue {spec['finance']['tax_per_point'] * delta:+.0%}"
            elif office == "war":
                effect = f"military morale {spec['war']['military_morale_per_point'] * delta:+.2f}/wk"
            else:
                effect = (f"recon {spec['intelligence']['recon_accuracy_per_point'] * delta:+.0%}, "
                          f"plot detection {court.detection_chance(game, court.Character('', '', 0, '', '', 0, 0, 0)):.0%}")
            cabinet.append(f" [{OFFICE_KEYS[office]}] {court.office_name(game, office).upper():<22}",
                           style=f"bold {palette.AMBER}")
            if holder:
                cabinet.append(f"{holder.name:<24}", style=palette.PHOSPHOR_BRIGHT)
                base = holder.stat(spec[office]["stat"])
                shown = f"{base}+{stat - base:g}" if stat != base else f"{base}"
                cabinet.append(f"{spec[office]['stat'][:3].upper()} {shown:<5}", style=palette.PHOSPHOR)
                cabinet.append(f"LOYALTY {holder.loyalty:>3.0f}  ", style=loyalty_style(holder.loyalty))
            else:
                cabinet.append(f"{'VACANT':<24}", style=f"bold {palette.RED}")
                cabinet.append(f"{'':<23}")
            cabinet.append(effect + "\n", style=palette.PHOSPHOR_DIM)
        self.query_one("#court-cabinet", Static).update(cabinet)

        table = self.query_one("#court-table", DataTable)
        cursor = table.cursor_row
        table.clear()
        order = {"Ruler": 0, "Spouse": 1, "Heir": 2, "Sibling": 3, "Cousin": 4, "Uncle/Aunt": 5, "Dowager": 6,
                 "Noble": 7}
        people = sorted(house.characters.values(), key=lambda c: (not c.alive, order.get(c.relation, 9), c.id))
        for char in people:
            status = self._status(char)
            post = (court.office_name(game, char.office) if char.office else
                    f"Commands {game.unit(char.unit_id).designation}" if char.unit_id and game.unit(char.unit_id)
                    else char.role)
            dim = not char.alive or char.imprisoned
            name_style = palette.PHOSPHOR_DIM if dim else (f"bold {palette.PHOSPHOR_BRIGHT}" if char.relation == RULER
                                                          else palette.PHOSPHOR_BRIGHT)
            table.add_row(
                Text(court.display_name(char).upper(), style=name_style),
                str(char.age), char.relation.upper(), Text(post[:30], style=palette.PHOSPHOR),
                str(char.administration), str(char.military), str(char.intrigue), f"{char.influence:.0f}",
                Text(court.trait_names(game, char), style=palette.AMBER),
                Text("—" if char.relation == RULER or not char.alive else
                     f"{char.loyalty:>3.0f} {palette.meter(char.loyalty, width=6)}", style=loyalty_style(char.loyalty)),
                status, key=char.id)
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
            self.char_id = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        self._render_detail()

        chronicle = Text()
        for entry in reversed(house.history):
            chronicle.append(f"WK {entry['turn']:03d} ", style=palette.AMBER)
            chronicle.append(entry["text"] + "\n", style=palette.PHOSPHOR)
        if not house.history:
            chronicle.append("The House has no history yet this war.", style=palette.PHOSPHOR_DIM)
        self.query_one("#court-history", Static).update(chronicle)

    def _status(self, char) -> Text:
        game = self.app.game
        if not char.alive:
            return Text(f"✝ {char.died or 'dead'}"[:34], style=palette.PHOSPHOR_DIM)
        if char.imprisoned:
            return Text("IN THE CITADEL", style=palette.AMBER)
        if char.married_to:
            from src.engine.diplomacy import nations

            return Text(f"MARRIED INTO {nations(game)[char.married_to]['adjective'].upper()}", style=palette.PHOSPHOR)
        if char.ill_weeks:
            return Text("GRAVELY ILL", style=f"bold {palette.RED}")
        if char.relation != RULER and char.loyalty < float(court.cfg(game).get("treason", {}).get("threshold", 20)):
            return Text("PLOTTING?", style=f"bold {palette.RED}")
        if char.unit_id:
            return Text("AT THE FRONT", style=palette.AMBER)
        return Text("at court", style=palette.PHOSPHOR_DIM)

    def _render_detail(self) -> None:
        game = self.app.game
        house = game.player.dynasty
        char = house.characters.get(self.char_id or "") if house else None
        if char is None:
            return
        detail = Text()
        detail.append(f"▸ {court.display_name(char).upper()}", style=f"bold {palette.PHOSPHOR_BRIGHT}")
        detail.append(f"  {char.role} · {char.relation.upper()} · AGE {char.age}\n\n", style=palette.PHOSPHOR_DIM)
        detail.append(f"ADMINISTRATION {char.administration:>2}   MILITARY {char.military:>2}   "
                      f"INTRIGUE {char.intrigue:>2}   INFLUENCE {char.influence:.0f}\n", style=palette.PHOSPHOR)
        if char.relation != RULER and char.alive:
            target = court.loyalty_target(game, char)
            detail.append(f"LOYALTY {char.loyalty:.0f}", style=loyalty_style(char.loyalty))
            detail.append(f"  (drifting toward {target:.0f})\n", style=palette.PHOSPHOR_DIM)
        cast = {m["id"]: m for m in game.catalog.get("dynasty", {}).get("family", [])}
        if char.id in cast and cast[char.id].get("description"):
            detail.append("\n" + cast[char.id]["description"] + "\n", style=palette.PHOSPHOR)
        detail.append("\nTRAITS\n", style=f"bold {palette.AMBER}")
        for t in char.traits:
            info = court.trait_info(game, t)
            detail.append(f"  {info['name']:<17}", style=palette.PHOSPHOR_BRIGHT)
            detail.append(f"{info.get('description', '')}\n", style=palette.PHOSPHOR_DIM)
        if not char.traits:
            detail.append("  none of note\n", style=palette.PHOSPHOR_DIM)
        line = court.line_of_succession(game)
        if char in line:
            detail.append(f"\nLINE OF SUCCESSION: #{line.index(char) + 1}\n", style=palette.AMBER)
        self.query_one("#court-detail", Static).update(detail)
