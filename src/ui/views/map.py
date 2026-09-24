"""The War Room: pannable situation map (left) + sector readout / intelligence panel (right)."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.intel import unit_report
from src.engine.map_overlay import Marker, displayed_type, grid_ref, marker_for_unit, units_in_region
from src.models import GameState, Unit
from src.ui import palette
from src.ui.widgets.map_canvas import MapCanvas

RULE = "─" * 34


def _templates(game: GameState) -> dict[str, dict]:
    return {t["id"]: t for t in game.catalog["units"]["units"]}


def _stances(game: GameState) -> dict[str, dict]:
    return {s["id"]: s for s in game.catalog["units"].get("stances", [])}


def _colors(game: GameState) -> tuple[str, str]:
    cfg = game.config.get("map", {})
    return cfg.get("friendly_color", "#4fd8ff"), cfg.get("hostile_color", "#ff3b3b")


def contact_code(game: GameState, unit: Unit) -> str:
    """Stable anonymous code for a hostile formation whose designation is unknown."""
    hostiles = sorted(u.id for u in game.all_units() if not game.is_friendly(u.nation_id))
    return f"CONTACT H-{hostiles.index(unit.id) + 1:02d}"


def _row(text: Text, label: str, value: str, style: str = palette.PHOSPHOR_BRIGHT) -> None:
    text.append(f"{label:<17}", style=palette.PHOSPHOR_DIM)
    text.append(value + "\n", style=style)


def _bar(value: float) -> str:
    return f"{value:.0f}%  {palette.meter(value)}"


def friendly_block(game: GameState, unit: Unit) -> Text:
    friendly, _ = _colors(game)
    template = _templates(game)[unit.unit_type]
    stance = _stances(game).get(unit.stance, {}).get("name", unit.stance)
    region = game.world_map.region_at(unit.x, unit.y)
    full = template["manpower"]
    text = Text()
    text.append(f"■ FRIENDLY FORMATION  {unit.designation}\n", style=f"bold {friendly}")
    text.append(f"{unit.name.upper()}\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
    _row(text, "TYPE", f"{template['name'].upper()}  [{template['symbol']}]")
    _row(text, "STRENGTH", f"{unit.strength:,} / {full:,}  ({unit.strength / full:.0%})")
    _row(text, "MORALE", _bar(unit.morale), palette.MORALE_STYLE.get(_band(unit.morale), palette.PHOSPHOR))
    supply_style = palette.PHOSPHOR_BRIGHT if unit.supply >= 75 else (palette.AMBER if unit.supply >= 40 else palette.RED)
    _row(text, "SUPPLY", _bar(unit.supply), supply_style)
    _row(text, "STANCE", stance.upper())
    _row(text, "COMMANDER", unit.commander or "—")
    _row(text, "POSITION", f"GRID {grid_ref(unit.x, unit.y)} · {region.name.upper() if region else 'AT SEA'}")
    return text


def hostile_block(game: GameState, unit: Unit) -> Text:
    _, hostile = _colors(game)
    report = unit_report(game, unit)
    template = _templates(game).get(report.reported_type, {})
    region = game.world_map.region_at(unit.x, unit.y)
    text = Text()
    title = f"{unit.designation} {unit.name.upper()} (PROBABLE)" if report.identified else "UNIDENTIFIED FORMATION"
    text.append(f"◆ HOSTILE {contact_code(game, unit)}\n", style=f"bold {hostile}")
    text.append(f"{title}\n", style=f"bold {palette.PHOSPHOR_BRIGHT}")
    _row(text, "TYPE (REPORTED)", f"{template.get('name', '?').upper()}  [{template.get('symbol', '?')}]")
    _row(text, "EST. STRENGTH", f"{report.strength.low:,} – {report.strength.high:,}", palette.AMBER)
    _row(text, "CERTAINTY", f"{report.accuracy:.0%}  {palette.meter(report.accuracy * 100)}", palette.AMBER)
    _row(text, "MORALE", "UNKNOWN", palette.PHOSPHOR_DIM)
    _row(text, "SUPPLY", "UNKNOWN", palette.PHOSPHOR_DIM)
    _row(text, "COMMANDER", "UNKNOWN", palette.PHOSPHOR_DIM)
    _row(text, "POSITION", f"GRID {grid_ref(unit.x, unit.y)} · {region.name.upper() if region else 'AT SEA'}")
    _row(text, "ASSESSED", f"WK {report.turn:03d} · REVISED WEEKLY", palette.PHOSPHOR_DIM)
    return text


def _band(value: float) -> str:
    from src.models.nation import morale_band

    return morale_band(value)


def sector_block(game: GameState, x: int, y: int) -> Text:
    world = game.world_map
    region = world.region_at(x, y)
    text = Text()
    text.append("SECTOR READOUT\n", style=f"bold {palette.AMBER}")
    _row(text, "GRID", grid_ref(x, y))
    feature = next((f for f in world.features if f.x == x and f.y == y), None)
    if feature:
        _row(text, "SETTLEMENT", f"{feature.name.upper()} ({feature.type.upper()})")
    if region is None:
        _row(text, "SECTOR", "OPEN WATER", palette.CYAN)
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
    _row(text, "FORCES", f"{friendly} FRIENDLY · {len(units) - friendly} HOSTILE CONTACT(S)")
    return text


def readout(game: GameState, x: int, y: int, marker: Marker | None) -> Text:
    text = Text()
    if marker is not None:
        if marker.is_stack:
            text.append(f"[*] STACK — {len(marker.units)} FORMATIONS\n", style=f"bold {palette.AMBER}")
            text.append(f"{RULE}\n", style=palette.PHOSPHOR_DIM)
        for unit in marker.units:
            block = friendly_block(game, unit) if game.is_friendly(unit.nation_id) else hostile_block(game, unit)
            text.append_text(block)
            text.append(f"{RULE}\n", style=palette.PHOSPHOR_DIM)
        text.append("\n")
    text.append_text(sector_block(game, x, y))
    return text


class MapView(Horizontal):
    """Left 70%: the pannable map. Right 30%: sector readout + order-of-battle list."""

    def compose(self) -> ComposeResult:
        with Vertical(id="map-pane"):
            yield MapCanvas(id="map-canvas", classes="primary-focus")
            yield Static(self._legend(), id="map-legend")
        with Vertical(id="intel-pane"):
            with VerticalScroll(id="intel-scroll"):
                yield Static(id="intel-readout")
            yield OptionList(id="orbat-list")

    def on_mount(self) -> None:
        self.query_one("#intel-pane").border_title = "SECTOR READOUT // INTEL"
        self.query_one("#orbat-list").border_title = "ORDER OF BATTLE"
        self._rebuild_orbat()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self._rebuild_orbat()

    def _legend(self) -> Text:
        game = self.app.game
        friendly, hostile = _colors(game)
        text = Text()
        for symbol, label in (("X", "INF"), ("O", "ARMOR"), ("•", "ARTY"), ("H", "HQ/LOG"), ("m", "MILITIA"), ("*", "STACK")):
            text.append(f"[{symbol}]", style=f"bold {palette.PHOSPHOR_BRIGHT}")
            text.append(f" {label}  ", style=palette.PHOSPHOR_DIM)
        text.append("■", style=f"bold {friendly}")
        text.append(" FRIENDLY  ", style=palette.PHOSPHOR_DIM)
        text.append("■", style=f"bold {hostile}")
        text.append(" HOSTILE\n", style=palette.PHOSPHOR_DIM)
        for glyph, label, style in (("★", "CAPITAL", "#ffd24a"), ("◉", "CITY", "#e8e8e8"), ("⊕", "PORT", "#e8e8e8"),
                                    ("═", "RAIL", "#9a7a3a"), ("┆", "TRENCH", "#6a5a2a"), ("≈", "RIVER/MARSH", "#2aa0a0"),
                                    ("^", "MOUNT", "#8a8f86"), ("│", "NATL BORDER", "#c08a10")):
            text.append(glyph, style=f"bold {style}")
            text.append(f" {label}  ", style=palette.PHOSPHOR_DIM)
        text.append("\nARROWS move · SHIFT+ARROWS ×5 · [ ] cycle units · C center · CLICK select · WHEEL pan",
                    style=palette.PHOSPHOR_DIM)
        return text

    # --- order of battle list ------------------------------------------------

    def _rebuild_orbat(self) -> None:
        game = self.app.game
        friendly_color, hostile_color = _colors(game)
        templates = _templates(game)
        options: list[Option] = [Option(Text("FRIENDLY FORMATIONS", style=f"bold {friendly_color}"), disabled=True)]
        hostiles = []
        for unit in sorted(game.all_units(), key=lambda u: u.designation):
            if not game.is_friendly(unit.nation_id):
                hostiles.append(unit)
                continue
            prompt = Text()
            prompt.append(f"[{templates[unit.unit_type]['symbol']}] ", style=f"bold {friendly_color}")
            prompt.append(f"{unit.designation} ", style=palette.PHOSPHOR_BRIGHT)
            prompt.append(unit.name, style=palette.PHOSPHOR)
            options.append(Option(prompt, id=unit.id))
        options.append(Option(Text("HOSTILE CONTACTS", style=f"bold {hostile_color}"), disabled=True))
        for unit in sorted(hostiles, key=lambda u: contact_code(game, u)):
            report = unit_report(game, unit)
            prompt = Text()
            prompt.append(f"[{templates[displayed_type(game, unit)]['symbol']}] ", style=f"bold {hostile_color}")
            prompt.append(f"{contact_code(game, unit)} ", style=palette.PHOSPHOR_BRIGHT)
            prompt.append(unit.name if report.identified else "unidentified", style=palette.PHOSPHOR_DIM)
            options.append(Option(prompt, id=unit.id))

        orbat = self.query_one("#orbat-list", OptionList)
        highlighted = orbat.highlighted_option.id if orbat.highlighted_option else None
        orbat.clear_options()
        orbat.add_options(options)
        if highlighted:
            orbat.highlighted = orbat.get_option_index(highlighted)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        unit = self.app.game.unit(event.option.id) if event.option.id else None
        if unit is None:
            return
        canvas = self.query_one(MapCanvas)
        marker = marker_for_unit(canvas.markers, unit.id)
        target = (marker.x, marker.y) if marker else unit.location
        if canvas.cursor != target:
            canvas.jump_to(*target)

    # --- readout -------------------------------------------------------------

    def on_map_canvas_cursor_moved(self, event: MapCanvas.CursorMoved) -> None:
        event.stop()
        game = self.app.game
        self.query_one("#intel-readout", Static).update(readout(game, event.x, event.y, event.marker))
        if event.marker is None:
            return
        orbat = self.query_one("#orbat-list", OptionList)
        current = orbat.highlighted_option.id if orbat.highlighted_option else None
        if current not in {u.id for u in event.marker.units}:
            orbat.highlighted = orbat.get_option_index(event.marker.units[0].id)
