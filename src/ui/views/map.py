"""The War Room: pannable situation map (left) + sector readout / intelligence panel (right).

Orders: select a friendly formation (cursor or Order of Battle list), press M to enter
targeting mode, move the cursor to the destination (the route and ETA preview live), and press
ENTER to issue. G types an exact grid reference instead. X cancels a standing order.
T cycles the selected formation's combat stance (DEFEND -> ASSAULT -> WITHDRAW), or a warship's
mission (PATROL -> BLOCKADE -> BOMBARD).
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

from src.engine.combat_engine import StanceError, set_stance
from src.engine.event_manager import GameOverError
from src.engine.logistics_engine import establishment, fill_ratio
from src.engine.intel import unit_report
from src.engine.map_overlay import (
    Marker,
    contact_code,
    displayed_type,
    grid_ref,
    marker_for_unit,
    units_in_region,
)
from src.engine.movement import SEA, OrderError, cancel_order, issue_move_order, plan_route
from src.engine.naval_engine import MissionError, blockading, in_port, next_mission, set_mission, ships_left
from src.engine.tick_engine import miles
from src.engine.recon import ghost_contacts
from src.models import Contact, GameState, Unit
from src.models.nation import morale_band
from src.ui import palette
from src.ui.screens.coordinates import CoordinatesScreen
from src.ui.widgets.map_canvas import MapCanvas

RULE = "─" * 34
STATUS_STYLE = {"holding": palette.PHOSPHOR, "moving": "#4fd8ff", "engaged": f"bold {palette.RED}",
                "routing": f"bold {palette.AMBER}"}
STANCE_STYLE = {"defend": palette.PHOSPHOR_BRIGHT, "assault": f"bold {palette.RED}", "withdraw": palette.AMBER}
STANCE_CYCLE = ("defend", "assault", "withdraw")
MISSION_STYLE = {"patrol": palette.PHOSPHOR_BRIGHT, "blockade": f"bold {palette.AMBER}", "bombard": f"bold {palette.RED}"}
MISSION_TEXT = {
    "patrol": "PATROL — hold station, engage enemy fleets",
    "blockade": "BLOCKADE — close enemy ports within 3 rows (30 mi)",
    "bombard": "BOMBARD — shell the coast for land battles within 2.5 rows",
}
SUPPLY_STATE_STYLE = {"supplied": palette.PHOSPHOR_BRIGHT, "overextended": palette.AMBER, "isolated": f"bold {palette.RED}"}


def _templates(game: GameState) -> dict[str, dict]:
    return {t["id"]: t for t in game.catalog["units"]["units"]}


def _stances(game: GameState) -> dict[str, dict]:
    return {s["id"]: s for s in game.catalog["units"].get("stances", [])}


def _colors(game: GameState) -> tuple[str, str]:
    cfg = game.config.get("map", {})
    return cfg.get("friendly_color", "#4fd8ff"), cfg.get("hostile_color", "#ff3b3b")


def _row(text: Text, label: str, value: str, style: str = palette.PHOSPHOR_BRIGHT) -> None:
    text.append(f"{label:<17}", style=palette.PHOSPHOR_DIM)
    text.append(value + "\n", style=style)


def _bar(value: float) -> str:
    return f"{value:.0f}%  {palette.meter(value)}"


def _order_text(game: GameState, unit: Unit) -> str:
    order = unit.active_order
    if order is None:
        return "NONE — HOLDING POSITION"
    route = plan_route(game, unit, order.target)
    eta = f" · {miles(game, route.cost):,.0f} MI · ETA {route.eta_weeks} WK" if route else " · NO ROUTE"
    return f"MOVE → {grid_ref(order.x, order.y)}{eta}"


def commander_text(game: GameState, unit: Unit) -> str:
    from src.engine.command import trait_names

    if not unit.commander:
        return "—"
    return f"{unit.commander} · {trait_names(game, unit) if unit.traits_known else 'TRAITS UNKNOWN'}"


def lost_block(game: GameState, unit: Unit) -> Text:
    from src.engine.electronic_warfare import last_report, zone_name, zone_of

    friendly, _ = _colors(game)
    report = last_report(game, unit)
    zone = game.jammed.get(zone_of(game, unit) or "", {})
    text = Text()
    text.append(f"? CONTACT LOST  {unit.designation}\n", style=f"bold {friendly}")
    text.append(f"{unit.name.upper()}\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
    _row(text, "SIGNAL", "NONE — SECTOR JAMMED", f"bold {palette.RED}")
    if zone:
        _row(text, "JAMMING", f"{zone_name(game, zone).upper()} · ~{zone['weeks']} WK", palette.AMBER)
    _row(text, "LAST REPORT", f"WK {report['turn']:03d} · GRID {grid_ref(report['x'], report['y'])}")
    for label in ("STRENGTH", "SUPPLY", "MORALE", "POSITION NOW"):
        _row(text, label, "UNKNOWN", palette.PHOSPHOR_DIM)
    text.append("The formation carries out its last orders. No new orders, stances or command changes can reach it "
                "until the jamming stops.\n", style=palette.PHOSPHOR_DIM)
    return text


def friendly_block(game: GameState, unit: Unit) -> Text:
    friendly, _ = _colors(game)
    template = _templates(game)[unit.unit_type]
    naval = template.get("domain") == SEA
    full = template["manpower"]
    text = Text()
    text.append(f"■ FRIENDLY {'SQUADRON' if naval else 'FORMATION'}  {unit.designation}\n", style=f"bold {friendly}")
    text.append(f"{unit.name.upper()}\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
    status = unit.status.upper() + (f" ({unit.routing_weeks} WK TO RALLY)" if unit.routing else "")
    if naval and in_port(game, unit) and unit.status == "holding":
        status += " · IN PORT"
    _row(text, "STATUS", status, STATUS_STYLE.get(unit.status, palette.PHOSPHOR))
    if naval:
        _row(text, "MISSION", MISSION_TEXT.get(unit.mission, unit.mission.upper()) + "  [T]",
             MISSION_STYLE.get(unit.mission, palette.PHOSPHOR))
        closing = blockading(game, unit)
        if unit.mission == "blockade":
            _row(text, "BLOCKADE", f"CLOSING {', '.join(p.upper() for p in closing)}" if closing
                 else "NO ENEMY PORT CLOSED (sail within 3 rows of one, uncontested)",
                 f"bold {palette.AMBER}" if closing else palette.PHOSPHOR_DIM)
        _row(text, "SHIPS", f"{ships_left(game, unit)} AFLOAT")
    else:
        _row(text, "STANCE", unit.stance.upper() + "  [T]", STANCE_STYLE.get(unit.stance, palette.PHOSPHOR))
    battle = next((b for b in game.battles.values() if b.active and unit.id in b.participants), None)
    if battle is not None:
        _row(text, "IN BATTLE", f"{battle.name.upper()} · WEEK {battle.weeks}", f"bold {palette.RED}")
    _row(text, "ORDER", _order_text(game, unit), "#4fd8ff" if unit.active_order else palette.PHOSPHOR)
    _row(text, "TYPE", f"{template['name'].upper()}  [{template['symbol']}]")
    _row(text, "STRENGTH", f"{unit.strength:,} / {full:,}  ({unit.strength / full:.0%})")
    _row(text, "MORALE", _bar(unit.morale), palette.MORALE_STYLE.get(morale_band(unit.morale), palette.PHOSPHOR))
    supply_style = palette.PHOSPHOR_BRIGHT if unit.supply >= 75 else (palette.AMBER if unit.supply >= 40 else palette.RED)
    _row(text, "SUPPLY", _bar(unit.supply), supply_style)
    line = unit.supply_state.upper() + (" · NO SUPPLY: ATTRITION, CANNOT ADVANCE" if unit.supply <= 0 else "")
    _row(text, "SUPPLY LINE", line, SUPPLY_STATE_STYLE.get(unit.supply_state, palette.PHOSPHOR))
    ammo = fill_ratio(game, unit)
    ammo_style = palette.PHOSPHOR_BRIGHT if ammo >= 0.5 else (palette.AMBER if ammo >= 0.25 else f"bold {palette.RED}")
    _row(text, "AMMUNITION", _bar(ammo * 100), ammo_style)
    from src.engine.movement import pace_mpd

    _row(text, "SPEED / RECON", f"{pace_mpd(game, unit):.0f} MI/DAY · SEES {miles(game, template.get('detection_radius', 5)):.0f} MI")
    _row(text, "COMMANDER", commander_text(game, unit))
    from src.engine.crisis_engine import disease, infection_of

    infection = infection_of(game, unit)
    if infection:
        state_text = "QUARANTINED" if infection["cure_in"] is not None else ("CORDONED" if infection["cordon"] else "SPREADING")
        _row(text, "HEALTH", f"[INFECTED] {disease(game, infection['disease'])['name'].upper()} · {state_text}",
             f"bold {palette.RED}")
    if unit.relief_weeks:
        _row(text, "DUTY", f"DISASTER RELIEF · {unit.relief_weeks} WK · CANNOT MOVE OR FIGHT", f"bold {palette.AMBER}")
    if unit.pending_orders:
        _row(text, "ORDERS", "SENT — AWAITING ACKNOWLEDGEMENT (next week)", palette.AMBER)
    if template.get("role") == "engineer":
        from src.engine.engineering import work_in_range

        work = work_in_range(game, unit)
        _row(text, "ENGINEERING", f"{len(work)} WRECKED SECTION(S) IN REACH" + (" · REPAIRING" if work and
             unit.status == "holding" and unit.supply_state == "supplied" else ""),
             palette.AMBER if work else palette.PHOSPHOR_DIM)
    _row(text, "POSITION", f"GRID {grid_ref(unit.x, unit.y)} · {game.world_map.place_name(unit.x, unit.y).upper()}")
    text.append("LOADOUT  carried / establishment\n", style=palette.PHOSPHOR_DIM)
    items = {e["id"]: e for e in game.catalog["equipment"]}
    for item_id, wanted in establishment(game, unit).items():
        have = unit.equipment_inventory.get(item_id, 0)
        style = palette.PHOSPHOR if have >= wanted * 0.5 else (palette.AMBER if have >= wanted * 0.25 else palette.RED)
        name = items.get(item_id, {}).get("name", item_id)
        text.append(f" {name[:17]:<17}{have:>7,}/{wanted:<6,}\n", style=style)
    return text


def hostile_block(game: GameState, unit: Unit) -> Text:
    _, hostile = _colors(game)
    report = unit_report(game, unit)
    template = _templates(game).get(report.reported_type, {})
    text = Text()
    title = f"{unit.designation} {unit.name.upper()} (PROBABLE)" if report.identified else "UNIDENTIFIED FORMATION"
    text.append(f"◆ HOSTILE {contact_code(game, unit)}\n", style=f"bold {hostile}")
    text.append(f"{title}\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
    if unit.engaged:
        _row(text, "STATUS", "ENGAGED WITH OUR FORCES", f"bold {palette.RED}")
    elif unit.routing:
        _row(text, "STATUS", "ROUTING — BROKEN", f"bold {palette.AMBER}")
    _row(text, "TYPE (REPORTED)", f"{template.get('name', '?').upper()}  [{template.get('symbol', '?')}]")
    _row(text, "EST. STRENGTH", f"{report.strength.low:,} – {report.strength.high:,}", palette.AMBER)
    _row(text, "CERTAINTY", f"{report.accuracy:.0%}  {palette.meter(report.accuracy * 100)}", palette.AMBER)
    _row(text, "MORALE", "UNKNOWN", palette.PHOSPHOR_DIM)
    _row(text, "SUPPLY", "UNKNOWN", palette.PHOSPHOR_DIM)
    _row(text, "INTENTIONS", "UNKNOWN", palette.PHOSPHOR_DIM)
    _row(text, "POSITION", f"GRID {grid_ref(unit.x, unit.y)} · {game.world_map.place_name(unit.x, unit.y).upper()}")
    _row(text, "ASSESSED", f"WK {report.turn:03d} · REVISED WEEKLY", palette.PHOSPHOR_DIM)
    return text


def ghost_block(game: GameState, contact: Contact) -> Text:
    _, hostile = _colors(game)
    unit = game.unit(contact.unit_id)
    text = Text()
    text.append(f"? LOST CONTACT {contact.code}\n", style=f"bold {hostile}")
    text.append("POSITION UNKNOWN — LAST KNOWN SHOWN\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
    _row(text, "LAST SEEN", f"WK {contact.last_seen_turn:03d} · GRID {grid_ref(contact.last_x, contact.last_y)}")
    if unit is not None:
        report = unit_report(game, unit)
        template = _templates(game).get(report.reported_type, {})
        _row(text, "LAST REPORTED", f"{template.get('name', '?').upper()}  [{template.get('symbol', '?')}]")
    weeks = game.clock.turn - contact.last_seen_turn
    _row(text, "TRAIL", f"{weeks} WEEK(S) COLD", palette.AMBER)
    text.append("Formation may have moved in any direction.\n", style=palette.PHOSPHOR_DIM)
    return text


def sector_block(game: GameState, x: int, y: int) -> Text:
    world = game.world_map
    region = world.region_at(x, y)
    text = Text()
    text.append("SECTOR READOUT\n", style=f"bold {palette.AMBER}")
    _row(text, "GRID", grid_ref(x, y))
    kind = world.transport_at(x, y)
    if kind in ("destroyed_rail", "destroyed_road"):
        _row(text, "INFRASTRUCTURE", f"{'RAILWAY' if kind == 'destroyed_rail' else 'ROAD'} DESTROYED — needs Combat "
             "Engineers", f"bold {palette.RED}")
    elif kind:
        _row(text, "INFRASTRUCTURE", kind.upper())
    feature = next((f for f in world.features if f.x == x and f.y == y), None)
    if feature:
        _row(text, "SETTLEMENT", f"{feature.name.upper()} ({feature.type.upper()})")
        from src.engine.crisis_engine import disease, infection_of

        infection = infection_of(game, feature.name)
        if infection:
            state_text = "QUARANTINED" if infection["cure_in"] is not None else ("CORDONED" if infection["cordon"] else "SPREADING")
            _row(text, "EPIDEMIC", f"[INFECTED] {disease(game, infection['disease'])['name'].upper()} · {state_text}",
                 f"bold {palette.RED}")
        if feature.type == "port":
            _row(text, "OVERSEAS TRADE", f"{feature.trade:,} {game.currency}/WK")
            holder = game.blockades.get(feature.name)
            _row(text, "HARBOUR", f"BLOCKADED BY {game.nations[holder].adjective.upper()} WARSHIPS" if holder else "OPEN",
                 f"bold {palette.RED}" if holder else palette.PHOSPHOR_BRIGHT)
    if region is None:
        _row(text, "SECTOR", f"OPEN WATER · {world.sea_zone_at(x, y).upper()}", palette.CYAN)
        text.append("Only warships sail here. Select a squadron, M to sail, T to set its mission.\n",
                    style=palette.PHOSPHOR_DIM)
        return text
    owner = region.owner
    owner_name = game.nations[owner].name.upper() if owner in game.nations else owner.upper()
    owner_style = {"kestria": palette.PHOSPHOR_BRIGHT, "vosk": palette.RED}.get(owner, palette.AMBER)
    terrain = world.terrain_info(region.terrain)
    _row(text, "SECTOR", region.name.upper() + ("  ★ CAPITAL" if region.capital else ""))
    _row(text, "CONTROL", owner_name, owner_style)
    _row(text, "TERRAIN", region.terrain.upper())
    _row(text, "MOVEMENT", f"×{terrain.get('movement', 1.0):.1f}")
    _row(text, "DEFENSE", f"×{terrain.get('defense', 1.0):.2f}")
    if terrain.get("description"):
        text.append(f"{terrain['description']}\n", style=palette.PHOSPHOR_DIM)
    units = units_in_region(game, region.id)
    friendly = sum(1 for u in units if game.is_friendly(u.nation_id))
    _row(text, "FORCES", f"{friendly} FRIENDLY · {len(units) - friendly} HOSTILE OBSERVED")
    from src.engine.air_engine import sector_status

    air = sector_status(game, region.id, game.player.id)
    _row(text, "AIR SITUATION", air, {"SUPERIORITY": palette.PHOSPHOR_BRIGHT, "DENIED": f"bold {palette.RED}",
                                      "CONTESTED": palette.AMBER}.get(air, palette.PHOSPHOR_DIM))
    return text


def readout(game: GameState, x: int, y: int, marker: Marker | None) -> Text:
    text = Text()
    if marker is not None and marker.ghost is not None:
        text.append_text(ghost_block(game, marker.ghost))
        text.append(f"{RULE}\n\n", style=palette.PHOSPHOR_DIM)
    elif marker is not None and marker.lost is not None:
        text.append_text(lost_block(game, marker.lost))
        text.append(f"{RULE}\n\n", style=palette.PHOSPHOR_DIM)
    elif marker is not None:
        if marker.is_stack:
            text.append(f"[*] STACK — {len(marker.units)} FORMATIONS\n", style=f"bold {palette.AMBER}")
            text.append(f"{RULE}\n", style=palette.PHOSPHOR_DIM)
        for unit in marker.units:
            block = friendly_block(game, unit) if game.is_friendly(unit.nation_id) else hostile_block(game, unit)
            text.append_text(block)
            text.append(f"{RULE}\n", style=palette.PHOSPHOR_DIM)
        text.append("\n")
    text.append_text(sector_block(game, x, y))
    if game.config.get("map", {}).get("debug_reveal_all"):  # main.py --reveal
        text.append("\nAI DEBUG (--reveal)\n", style=f"bold {palette.RED}")
        for ai in game.ai_states.values():
            _row(text, ai.nation_id.upper(), f"{ai.posture} · TENSION {ai.tension:.0f} · FOCUS ROW {ai.focus_y}",
                 palette.RED)
    return text


class MapView(Horizontal):
    """Left 70%: the pannable map. Right 30%: sector readout + order-of-battle list."""

    BINDINGS = [
        Binding("m,o", "move_order", "Move Order"),
        Binding("g", "grid_order", "Order to Grid"),
        Binding("x", "cancel_order", "Cancel Order"),
        Binding("s", "toggle_supply", "Supply Overlay"),
        Binding("t", "cycle_stance", "Stance"),
        Binding("enter", "confirm_order", "Confirm", show=False),
        Binding("escape", "abort_targeting", "Abort", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.selected_unit_id: str | None = None
        self.targeting_unit_id: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="map-pane"):
            yield MapCanvas(id="map-canvas", classes="primary-focus")
            yield Static(id="order-bar")
            yield Static(self._legend(), id="map-legend")
        with Vertical(id="intel-pane"):
            with VerticalScroll(id="intel-scroll"):
                yield Static(id="intel-readout")
            yield OptionList(id="orbat-list")

    def on_mount(self) -> None:
        self.query_one("#intel-pane").border_title = "SECTOR READOUT // INTEL"
        self.query_one("#orbat-list").border_title = "ORDER OF BATTLE"
        self._rebuild_orbat()
        self._update_order_bar()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self._rebuild_orbat()
        if self.targeting_unit_id and self.app.game.game_over:
            self.action_abort_targeting()
        self._update_route_preview()
        self._update_order_bar()

    @property
    def canvas(self) -> MapCanvas:
        return self.query_one(MapCanvas)

    def _legend(self) -> Text:
        game = self.app.game
        friendly, hostile = _colors(game)
        text = Text()
        for symbol, label in (("X", "INF"), ("O", "ARMOR"), ("•", "ARTY"), ("H", "HQ"), ("m", "MILITIA"),
                              ("D", "DESTROYERS"), ("B", "BATTLESHIPS"), ("U", "SUBS"), ("*", "STACK"), ("?", "LOST")):
            text.append(f"[{symbol}]", style=f"bold {palette.PHOSPHOR_BRIGHT}")
            text.append(f" {label}  ", style=palette.PHOSPHOR_DIM)
        text.append("■", style=f"bold {friendly}")
        text.append(" FRIENDLY  ", style=palette.PHOSPHOR_DIM)
        text.append("■", style=f"bold {hostile}")
        text.append(" HOSTILE\n", style=palette.PHOSPHOR_DIM)
        for glyph, label, style in (("★", "CAPITAL", "#ffd24a"), ("◉", "CITY", "#e8e8e8"), ("▣", "INDUSTRY", "#d0b070"),
                                    ("⊕", "PORT", "#e8e8e8"), ("○", "TOWN", "#b8b8b8"), ("═", "RAIL", "#9a7a3a"),
                                    ("┈", "ROAD", "#7a6a48"), ("┆", "TRENCH", "#6a5a2a"), ("≈", "MARSH/RIVER", "#2aa0a0"),
                                    ("∩", "HILLS", "#8a7a5a"), ("^", "MOUNT", "#8a8f86"), ("~", "SEA", "#1f5670"),
                                    ("◇", "ORDER", friendly)):
            text.append(glyph, style=f"bold {style}")
            text.append(f" {label}  ", style=palette.PHOSPHOR_DIM)
        text.append("\nARROWS · SHIFT ×5 · [ ] cycle · C center · CLICK · M/O move · G grid · X cancel · S supply · "
                    "T stance/mission · 1 ROW = 10 MI", style=palette.PHOSPHOR_DIM)
        return text

    # --- selection -----------------------------------------------------------

    def _selected_friendly(self) -> Unit | None:
        from src.engine.electronic_warfare import is_dark

        game = self.app.game
        unit = game.unit(self.selected_unit_id) if self.selected_unit_id else None
        return unit if unit is not None and game.is_friendly(unit.nation_id) and not is_dark(game, unit) else None

    def _update_order_bar(self) -> None:
        game = self.app.game
        bar = Text()
        if self.targeting_unit_id:
            unit = game.unit(self.targeting_unit_id)
            x, y = self.canvas.cursor
            route = plan_route(game, unit, (x, y)) if unit else None
            bar.append(" TARGETING ", style=f"bold #000000 on #4fd8ff")
            bar.append(f" {unit.designation} → GRID {grid_ref(x, y)}  ", style=f"bold {palette.PHOSPHOR_BRIGHT}")
            if route is None:
                naval = unit is not None and game.domain(unit) == SEA
                bar.append("NO SEA ROUTE (open water only)" if naval else "NO LAND ROUTE", style=f"bold {palette.RED}")
            elif not route.path:
                bar.append("ALREADY THERE", style=palette.AMBER)
            else:
                bar.append(f"ETA {route.eta_weeks} WK · {miles(game, route.cost):,.0f} MI", style="#4fd8ff")
            bar.append("   ENTER issue · G type grid · ESC abort", style=palette.PHOSPHOR_DIM)
        else:
            unit = self._selected_friendly()
            if unit is not None:
                bar.append(" SELECTED ", style=f"bold #000000 on {palette.PHOSPHOR_DIM}")
                bar.append(f" {unit.designation} {unit.name.upper()}  ", style=palette.PHOSPHOR_BRIGHT)
                bar.append(_order_text(game, unit), style="#4fd8ff" if unit.active_order else palette.PHOSPHOR_DIM)
                if game.domain(unit) == SEA:
                    bar.append(f"  MISSION {unit.mission.upper()}", style=MISSION_STYLE.get(unit.mission, palette.PHOSPHOR))
                    bar.append("   M sail · G grid · X cancel · T mission", style=palette.PHOSPHOR_DIM)
                else:
                    bar.append(f"  STANCE {unit.stance.upper()}", style=STANCE_STYLE.get(unit.stance, palette.PHOSPHOR))
                    bar.append("   M move · G grid · X cancel · T stance", style=palette.PHOSPHOR_DIM)
            else:
                bar.append(" NO FORMATION SELECTED ", style=f"{palette.PHOSPHOR_DIM}")
                bar.append("  place the cursor on a friendly [symbol] or pick one in the Order of Battle",
                           style=palette.PHOSPHOR_DIM)
        self.query_one("#order-bar", Static).update(bar)

    def _update_route_preview(self) -> None:
        game = self.app.game
        canvas = self.canvas
        if self.targeting_unit_id:
            unit = game.unit(self.targeting_unit_id)
            route = plan_route(game, unit, canvas.cursor) if unit else None
            canvas.show_route(route.path if route else [])
            return
        unit = self._selected_friendly()
        if unit is not None and unit.active_order:
            route = plan_route(game, unit, unit.active_order.target)
            canvas.show_route(route.path if route else [])
        else:
            canvas.show_route([])

    # --- orders --------------------------------------------------------------

    def _order_guard(self) -> Unit | None:
        game = self.app.game
        if game.game_over:
            self.notify("The terminal is locked.", severity="error")
            return None
        unit = self._selected_friendly()
        if unit is None:
            self.notify("Select a friendly formation first.", severity="warning")
        return unit

    def action_move_order(self) -> None:
        if self.targeting_unit_id:
            return
        unit = self._order_guard()
        if unit is None:
            return
        self.targeting_unit_id = unit.id
        self.canvas.targeting = True
        self.canvas.focus()
        self._update_route_preview()
        self._update_order_bar()

    def action_grid_order(self) -> None:
        unit = self.app.game.unit(self.targeting_unit_id) if self.targeting_unit_id else self._order_guard()
        if unit is None:
            return
        world = self.app.game.world_map

        def on_coords(coords: tuple[int, int] | None) -> None:
            if coords is None:
                return
            if not self.targeting_unit_id:
                self.targeting_unit_id = unit.id
                self.canvas.targeting = True
            self.canvas.focus()
            self.canvas.jump_to(*coords)  # preview; ENTER confirms

        label = f"{unit.designation} {unit.name}"
        self.app.push_screen(CoordinatesScreen(label, world.width, world.height, self.canvas.cursor), on_coords)

    def action_confirm_order(self) -> None:
        if not self.targeting_unit_id:
            return
        game = self.app.game
        unit_id = self.targeting_unit_id
        target = self.canvas.cursor
        try:
            route = issue_move_order(game, unit_id, target)
        except (OrderError, GameOverError) as error:
            self.notify(str(error), title="ORDER REJECTED", severity="error")
            return
        unit = game.unit(unit_id)
        self.action_abort_targeting()
        self.selected_unit_id = unit_id
        self.app.state_changed()
        if route.path:
            self.notify(f"{unit.designation} → GRID {grid_ref(*target)} · ETA {route.eta_weeks} WK",
                        title="MOVE ORDER ISSUED")
        else:
            self.notify(f"{unit.designation} will hold position.", title="ORDER UPDATED")

    def action_cancel_order(self) -> None:
        unit = self._order_guard()
        if unit is None:
            return
        if unit.active_order is None:
            self.notify(f"{unit.designation} has no standing order.", severity="warning")
            return
        cancel_order(self.app.game, unit.id)
        self.app.state_changed()
        self.notify(f"{unit.designation} will hold position.", title="ORDER CANCELLED")

    def action_cycle_stance(self) -> None:
        unit = self._order_guard()
        if unit is None:
            return
        if self.app.game.domain(unit) == SEA:
            mission = next_mission(unit.mission)
            try:
                set_mission(self.app.game, unit.id, mission)
            except MissionError as error:
                self.notify(str(error), title="MISSION", severity="warning")
                return
            self.app.state_changed()
            self.notify(f"{unit.designation} → {MISSION_TEXT[mission]}", title="NAVAL MISSION")
            return
        nxt = STANCE_CYCLE[(STANCE_CYCLE.index(unit.stance) + 1) % len(STANCE_CYCLE)] if unit.stance in STANCE_CYCLE else "defend"
        try:
            set_stance(self.app.game, unit.id, nxt)
        except StanceError as error:
            self.notify(str(error), title="STANCE", severity="warning")
            return
        self.app.state_changed()
        description = _stances(self.app.game).get(nxt, {}).get("description", "")
        self.notify(f"{unit.designation} → {nxt.upper()}. {description}", title="STANCE ORDER")

    def action_toggle_supply(self) -> None:
        canvas = self.canvas
        canvas.supply_overlay = not canvas.supply_overlay
        state = "ON — blue: our supply net · red: enemy zones of control" if canvas.supply_overlay else "OFF"
        self.notify(f"Supply overlay {state}", title="LOGISTICS")

    def action_abort_targeting(self) -> None:
        if not self.targeting_unit_id:
            return
        self.targeting_unit_id = None
        self.canvas.targeting = False
        self._update_route_preview()
        self._update_order_bar()

    # --- order of battle list ------------------------------------------------

    def _rebuild_orbat(self) -> None:
        game = self.app.game
        friendly_color, hostile_color = _colors(game)
        templates = _templates(game)
        options: list[Option] = [Option(Text("FRIENDLY FORMATIONS", style=f"bold {friendly_color}"), disabled=True)]
        from src.engine.electronic_warfare import is_dark

        dark = [u for u in game.player.units if is_dark(game, u)]
        if dark:
            options.append(Option(Text(f"  ! SIGNAL LOST: {len(dark)} formation(s) out of contact",
                                       style=f"bold {palette.RED}"), disabled=True))
        for unit in sorted((u for u in game.player.units if u not in dark), key=lambda u: u.designation):
            prompt = Text()
            prompt.append(f"[{templates[unit.unit_type]['symbol']}] ", style=f"bold {friendly_color}")
            prompt.append(f"{unit.designation} ", style=palette.PHOSPHOR_BRIGHT)
            prompt.append(unit.name, style=palette.PHOSPHOR)
            if game.domain(unit) == SEA:
                prompt.append(f"  {unit.mission.upper()}", style=MISSION_STYLE.get(unit.mission, palette.PHOSPHOR))
            if f"unit:{unit.id}" in game.infections:
                prompt.append("  [INFECTED]", style=f"bold {palette.RED}")
            if unit.relief_weeks:
                prompt.append(f"  RELIEF {unit.relief_weeks}WK", style=f"bold {palette.AMBER}")
            if unit.supply_state != "supplied":
                prompt.append(f"  {unit.supply_state.upper()}", style=SUPPLY_STATE_STYLE[unit.supply_state])
            if unit.routing:
                prompt.append("  ROUTING", style=f"bold {palette.AMBER}")
            elif unit.engaged:
                prompt.append(f"  ENGAGED · {unit.stance.upper()}", style=f"bold {palette.RED}")
            elif unit.active_order:
                prompt.append(f"  → {grid_ref(unit.active_order.x, unit.active_order.y)}", style="#4fd8ff")
            options.append(Option(prompt, id=unit.id))

        options.append(Option(Text("HOSTILE CONTACTS", style=f"bold {hostile_color}"), disabled=True))
        visible = sorted(game.visible_hostiles(), key=lambda u: contact_code(game, u))
        if not visible:
            options.append(Option(Text("  none observed", style=palette.PHOSPHOR_DIM), disabled=True))
        for unit in visible:
            report = unit_report(game, unit)
            prompt = Text()
            prompt.append(f"[{templates[displayed_type(game, unit)]['symbol']}] ", style=f"bold {hostile_color}")
            prompt.append(f"{contact_code(game, unit)} ", style=palette.PHOSPHOR_BRIGHT)
            prompt.append(unit.name if report.identified else "unidentified", style=palette.PHOSPHOR_DIM)
            if unit.engaged:
                prompt.append("  ENGAGED", style=f"bold {palette.RED}")
            options.append(Option(prompt, id=unit.id))
        if not game.config.get("map", {}).get("debug_reveal_all"):
            for contact in sorted(ghost_contacts(game), key=lambda c: c.code):
                prompt = Text()
                prompt.append("[?] ", style="#8a3a3a")
                prompt.append(f"CONTACT {contact.code} ", style=palette.PHOSPHOR_DIM)
                prompt.append(f"lost · last seen WK {contact.last_seen_turn:03d}", style=palette.PHOSPHOR_DIM)
                options.append(Option(prompt, id=f"ghost:{contact.unit_id}"))

        orbat = self.query_one("#orbat-list", OptionList)
        highlighted = orbat.highlighted_option.id if orbat.highlighted_option else None
        orbat.clear_options()
        orbat.add_options(options)
        if highlighted:
            try:
                orbat.highlighted = orbat.get_option_index(highlighted)
            except OptionDoesNotExist:  # the option vanished (contact lost); leave nothing highlighted
                pass

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        option_id = event.option.id
        if not option_id:
            return
        game = self.app.game
        canvas = self.canvas
        if option_id.startswith("ghost:"):
            contact = game.contacts.get(option_id.split(":", 1)[1])
            target = (contact.last_x, contact.last_y) if contact else None
        else:
            unit = game.unit(option_id)
            if unit is None:
                return
            if not self.targeting_unit_id:
                self.selected_unit_id = unit.id
            marker = marker_for_unit(canvas.markers, unit.id)
            target = (marker.x, marker.y) if marker else unit.location
        if target and canvas.cursor != target:
            canvas.jump_to(*target)
        self._update_route_preview()
        self._update_order_bar()

    # --- readout -------------------------------------------------------------

    def on_map_canvas_cursor_moved(self, event: MapCanvas.CursorMoved) -> None:
        event.stop()
        game = self.app.game
        marker = event.marker
        self.query_one("#intel-readout", Static).update(readout(game, event.x, event.y, marker))
        if self.targeting_unit_id:
            self._update_route_preview()
            self._update_order_bar()
            return
        if marker is not None and marker.units:
            ids = [u.id for u in marker.units]
            if self.selected_unit_id not in ids:
                friendly = [u.id for u in marker.units if game.is_friendly(u.nation_id)]
                self.selected_unit_id = (friendly or ids)[0]
            orbat = self.query_one("#orbat-list", OptionList)
            current = orbat.highlighted_option.id if orbat.highlighted_option else None
            if current != self.selected_unit_id:
                try:
                    orbat.highlighted = orbat.get_option_index(self.selected_unit_id)
                except OptionDoesNotExist:
                    pass
        else:
            self.selected_unit_id = None
        self._update_route_preview()
        self._update_order_bar()
