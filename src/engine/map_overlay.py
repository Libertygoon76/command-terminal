"""War Room overlay: turns unit positions into map markers.

Pure logic (no Textual): which symbol goes where, how overlapping units stack, and what
sits under a given cell. The UI decides colors and draws the markers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.intel import unit_report
from src.models import GameState, Unit

FRIENDLY = "friendly"
HOSTILE = "hostile"
MIXED = "mixed"


@dataclass
class Marker:
    """One symbol on the map: a single unit, or a stack of units drawn as [*]."""

    x: int  # centre cell; the symbol covers x-1 .. x+1
    y: int
    units: list[Unit] = field(default_factory=list)
    glyph: str = "?"
    side: str = FRIENDLY

    @property
    def is_stack(self) -> bool:
        return len(self.units) > 1

    @property
    def text(self) -> str:
        return f"[{self.glyph}]"

    def covers(self, x: int, y: int) -> bool:
        return y == self.y and self.x - 1 <= x <= self.x + 1

    def overlaps(self, x: int, y: int) -> bool:
        """Would a symbol centred on (x, y) collide with this one?"""
        return y == self.y and abs(x - self.x) <= 2


def displayed_type(state: GameState, unit: Unit) -> str:
    """The unit type the player sees: truth for friendlies, the intel report for hostiles."""
    if state.is_friendly(unit.nation_id):
        return unit.unit_type
    return unit_report(state, unit).reported_type


def unit_glyph(state: GameState, unit: Unit) -> str:
    templates = {t["id"]: t for t in state.catalog["units"]["units"]}
    return templates.get(displayed_type(state, unit), {}).get("symbol", "?")


def build_markers(state: GameState) -> list[Marker]:
    """Place every unit on the map. Units whose symbols would overlap merge into one stack."""
    stack_glyph = state.config.get("map", {}).get("stack_symbol", "*")
    markers: list[Marker] = []
    # Deterministic order: friendly first, then by position.
    units = sorted(state.all_units(), key=lambda u: (not state.is_friendly(u.nation_id), u.y, u.x, u.id))
    for unit in units:
        side = FRIENDLY if state.is_friendly(unit.nation_id) else HOSTILE
        host = next((m for m in markers if m.overlaps(unit.x, unit.y)), None)
        if host is None:
            markers.append(Marker(unit.x, unit.y, [unit], unit_glyph(state, unit), side))
            continue
        host.units.append(unit)
        host.glyph = stack_glyph
        if host.side != side:
            host.side = MIXED
    return markers


def marker_at(markers: list[Marker], x: int, y: int) -> Marker | None:
    return next((m for m in markers if m.covers(x, y)), None)


def marker_for_unit(markers: list[Marker], unit_id: str) -> Marker | None:
    return next((m for m in markers if any(u.id == unit_id for u in m.units)), None)


def units_in_region(state: GameState, region_id: str) -> list[Unit]:
    world = state.world_map
    return [u for u in state.all_units() if (r := world.region_at(u.x, u.y)) is not None and r.id == region_id]


def grid_ref(x: int, y: int) -> str:
    return f"{x:03d}-{y:03d}"
