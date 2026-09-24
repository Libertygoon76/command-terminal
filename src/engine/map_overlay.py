"""War Room overlay: turns unit positions into map markers.

Pure logic (no Textual): which symbol goes where, how overlapping units stack, and what
sits under a given cell. The UI decides colors and draws the markers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.intel import unit_report
from src.engine.recon import ghost_contacts
from src.models import Contact, GameState, Unit

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
    ghost: Contact | None = None  # set for a lost contact's last-known-position marker
    lost: Unit | None = None  # a friendly formation blacked out by jamming, drawn at its last report

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
    """Place every unit the player can see. Overlapping symbols merge into one stack.

    Hostile units outside detection range are omitted; lost contacts get a dim `[?]` ghost at
    their last known position (unless something real is drawn there).
    """
    from src.engine.electronic_warfare import is_dark, last_report

    stack_glyph = state.config.get("map", {}).get("stack_symbol", "*")
    markers: list[Marker] = []
    dark = [u for u in state.player.units if is_dark(state, u)]
    shown = [u for u in state.player.units if u not in dark] + state.visible_hostiles()
    # Deterministic order: friendly first, then by position.
    units = sorted(shown, key=lambda u: (not state.is_friendly(u.nation_id), u.y, u.x, u.id))
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
    for unit in sorted(dark, key=lambda u: u.id):  # CONTACT LOST: our own formations in a jammed zone
        report = last_report(state, unit)
        x, y = report["x"], report["y"]
        if not any(m.overlaps(x, y) for m in markers):
            markers.append(Marker(x, y, [], "?", FRIENDLY, lost=unit))
    if not state.config.get("map", {}).get("debug_reveal_all"):
        for contact in sorted(ghost_contacts(state), key=lambda c: c.code):
            if not any(m.overlaps(contact.last_x, contact.last_y) for m in markers):
                markers.append(Marker(contact.last_x, contact.last_y, [], "?", HOSTILE, ghost=contact))
    return markers


def marker_at(markers: list[Marker], x: int, y: int) -> Marker | None:
    return next((m for m in markers if m.covers(x, y)), None)


def marker_for_unit(markers: list[Marker], unit_id: str) -> Marker | None:
    return next((m for m in markers if any(u.id == unit_id for u in m.units)), None)


def units_in_region(state: GameState, region_id: str) -> list[Unit]:
    """Units the player knows are in a region: all friendlies, visible hostiles only."""
    world = state.world_map
    from src.engine.electronic_warfare import reachable_units

    known = reachable_units(state) + state.visible_hostiles()
    return [u for u in known if (r := world.region_at(u.x, u.y)) is not None and r.id == region_id]


def contact_code(state: GameState, unit: Unit) -> str:
    contact = state.contacts.get(unit.id)
    return f"CONTACT {contact.code}" if contact else "UNTRACKED CONTACT"


def hostile_label(state: GameState, unit: Unit, for_intercept: bool = False) -> str:
    """How Kestrian reports refer to a hostile formation."""
    if for_intercept:  # SIGINT reads call signs: it names the unit (the text may still be redacted)
        return f"{unit.designation} {unit.name}"
    report = unit_report(state, unit)
    templates = {t["id"]: t for t in state.catalog["units"]["units"]}
    if report.identified:
        return f"the Vosk {unit.name} (probable)"
    kind = templates.get(report.reported_type, {}).get("name", "formation").lower()
    return f"an unidentified Vosk {kind} ({contact_code(state, unit)})"


def grid_ref(x: int, y: int) -> str:
    return f"{x:03d}-{y:03d}"
