"""Active fog of war: which hostile formations Kestrian forces can currently see.

A hostile unit is visible if it lies within the detection radius of at least one friendly
formation (radius from units.json `detection_radius`, measured in rows; columns count at
map.column_scale). Every hostile unit ever seen gets a Contact with an anonymous code and
its last known position. When contact is lost, the map keeps a ghost marker for
map.ghost_weeks, then the trail goes cold.
"""

from __future__ import annotations

import math

from src.engine.systems import SimulationSystem, TickReport
from src.models import Contact, GameState, Unit


def distance(state: GameState, a: tuple[int, int], b: tuple[int, int]) -> float:
    col = float(state.config.get("map", {}).get("column_scale", 0.5))
    return math.hypot((a[0] - b[0]) * col, a[1] - b[1])


def detection_radius(state: GameState, unit: Unit) -> float:
    return float(state.template(unit.unit_type).get("detection_radius", 5))


def sighting_range(state: GameState, observer: Unit, target: Unit) -> float:
    """Submarines (template `stealth`) are seen at half range, except by ASW ships (destroyers)."""
    radius = detection_radius(state, observer)
    if state.template(target.unit_type).get("stealth") and not state.template(observer.unit_type).get("asw"):
        radius *= float(state.config.get("naval", {}).get("submarine_stealth", 0.5))
    return radius


def is_detected(state: GameState, hostile: Unit) -> bool:
    return any(
        distance(state, f.location, hostile.location) <= sighting_range(state, f, hostile) for f in state.player.units
    ) or bool(hostile.engaged_with)


def update_contacts(state: GameState) -> tuple[list[Contact], list[Contact]]:
    """Refresh every contact. Returns (newly acquired, newly lost) this update."""
    acquired, lost = [], []
    turn = state.clock.turn
    for unit in state.hostile_units():
        contact = state.contacts.get(unit.id)
        if is_detected(state, unit):
            if contact is None:
                code = f"H-{len(state.contacts) + 1:02d}"
                contact = Contact(code, unit.id, unit.x, unit.y, turn, visible=True)
                state.contacts[unit.id] = contact
                acquired.append(contact)
            else:
                if not contact.visible:
                    acquired.append(contact)
                contact.visible = True
                contact.last_x, contact.last_y, contact.last_seen_turn = unit.x, unit.y, turn
        elif contact is not None and contact.visible:
            contact.visible = False
            lost.append(contact)
    return acquired, lost


def ghost_contacts(state: GameState) -> list[Contact]:
    """Lost contacts still worth plotting (last seen within map.ghost_weeks)."""
    weeks = int(state.config.get("map", {}).get("ghost_weeks", 4))
    return [c for c in state.contacts.values() if not c.visible and state.clock.turn - c.last_seen_turn <= weeks]


class ReconSystem(SimulationSystem):
    """Runs after movement: updates what the Kestrian General Staff can see."""

    name = "recon"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        acquired, lost = update_contacts(state)
        if acquired:
            report.log.append(f"{len(acquired)} hostile contact(s) acquired.")
        if lost:
            report.log.append(f"Contact lost with {len(lost)} hostile formation(s).")
