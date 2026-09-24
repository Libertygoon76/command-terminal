"""Scorched earth and combat engineers: the transport layer can now be destroyed and rebuilt.

SABOTAGE — a formation that ROUTS (scorched_earth.rout_chance) or falls back in WITHDRAW stance
(withdraw_chance, each week it retreats) may blow the railways and roads behind it: between cells[0] and
cells[1] rail/road cells within `radius` rows of where it stood become `destroyed_rail` (drawn `x`) or
`destroyed_road`. Destroyed transport gives NO movement or supply bonus: an army that loses its railhead
crawls, and so does its supply.

REPAIR — Combat Engineer Battalions (template role "engineer") that hold position inside their supply net
rebuild engineering.repair_per_week destroyed cells (nearest first) within engineering.repair_range rows
each week. They can also rebuild the rubble rail bed across no-man's-land.

The damage is part of the campaign state (`state.map_damage`: cell -> current kind, wherever it differs
from world.json), so it is saved and loaded with the game. Changing the transport layer drops the cached
movement/supply cost tables and routes.
"""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.movement import scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import ENGAGED, HOLDING, Email, GameState, Unit

DESTROYED = {"rail": "destroyed_rail", "road": "destroyed_road"}
REPAIRED = {"destroyed_rail": "rail", "destroyed_road": "road"}
TERRAIN_CACHES = ("move_costs", "supply_entry_costs", "cost_arrays")


def invalidate_terrain_caches(state: GameState, cells: list[tuple[int, int]] | None = None) -> None:
    """Bring every cache derived from the transport layer up to date after `cells` changed.

    The entry cost of a cell depends only on that cell, so the movement and supply cost tables (and their
    flattened arrays) are patched in place; cached routes are dropped. Without `cells`, everything derived
    is thrown away and rebuilt on demand."""
    state.cost_cache.pop("routes", None)
    if cells is None:
        for key in list(state.cost_cache):
            if isinstance(key, tuple) and key and key[0] in TERRAIN_CACHES:
                del state.cost_cache[key]
        return
    from src.engine.logistics_engine import supply_step_cost
    from src.engine.movement import step_cost

    width = state.world_map.width
    for key, table in list(state.cost_cache.items()):
        if not (isinstance(key, tuple) and key):
            continue
        if key[0] == "move_costs" and key[1] == "land":
            for (x, y) in cells:
                if (x, y) in table:
                    table[(x, y)] = (step_cost(state, (x - 1, y), (x, y)), step_cost(state, (x, y - 1), (x, y)))
            arrays = state.cost_cache.get(("cost_arrays", ("move", "land")))
        elif key[0] == "supply_entry_costs":
            nation_id = key[1]
            for (x, y) in cells:
                if (x, y) in table:
                    table[(x, y)] = (supply_step_cost(state, nation_id, (x - 1, y), (x, y)),
                                     supply_step_cost(state, nation_id, (x, y - 1), (x, y)))
            arrays = state.cost_cache.get(("cost_arrays", ("supply", nation_id)))
        else:
            continue
        if arrays is not None:
            hc, vc = arrays
            for (x, y) in cells:
                if (x, y) in table:
                    hc[y * width + x], vc[y * width + x] = table[(x, y)]


def set_cell(state: GameState, cell: tuple[int, int], kind: str) -> None:
    world = state.world_map
    world.set_transport(cell, kind)
    if world.base_transport.get(cell) == kind:
        state.map_damage.pop(cell, None)
    else:
        state.map_damage[cell] = kind


def apply_damage(state: GameState) -> None:
    """Re-apply saved map damage to a freshly loaded world map."""
    for cell, kind in state.map_damage.items():
        state.world_map.set_transport(cell, kind)
    invalidate_terrain_caches(state)


def damaged_cells(state: GameState) -> list[tuple[int, int]]:
    return [c for c, kind in state.world_map.transport.items() if kind in REPAIRED]


def sabotage(state: GameState, unit: Unit, origin: tuple[int, int], chance: float,
             report: TickReport | None = None) -> list[tuple[int, int]]:
    """Maybe blow up the rail and road around `origin` as `unit` leaves it. Returns destroyed cells."""
    cfg = state.config.get("scorched_earth", {})
    if chance <= 0 or state.rng.random() >= chance:
        return []
    radius = float(cfg.get("radius", 2.0))
    world = state.world_map
    x0, y0 = origin
    span = int(radius / 0.5) + 1
    candidates = sorted(
        ((scaled_distance(state, (x, y), origin), (x, y)) for x in range(x0 - span, x0 + span + 1)
         for y in range(y0 - int(radius), y0 + int(radius) + 1)
         if world.transport_at(x, y) in DESTROYED and scaled_distance(state, (x, y), origin) <= radius),
    )
    if not candidates:
        return []
    lo, hi = cfg.get("cells", [3, 6])
    count = state.rng.randint(int(lo), int(hi))
    destroyed = [cell for _, cell in candidates[:count]]
    kinds = set()
    for cell in destroyed:
        kinds.add(world.transport_at(*cell))
        set_cell(state, cell, DESTROYED[world.transport_at(*cell)])
    invalidate_terrain_caches(state, destroyed)
    if report is not None:
        ours = state.is_friendly(unit.nation_id)
        in_our_land = any(world.owner_at(*c) == state.player.id for c in destroyed)
        what = "railway" if "rail" in kinds else "road"
        report.log.append(f"SCORCHED EARTH: {len(destroyed)} cells of {what} destroyed near grid {x0:03d}-{y0:03d}.")
        if ours or in_our_land:
            report.new_messages.append(deliver(state, scorched_email(state, unit, origin, destroyed, what)))
    return destroyed


def scorched_email(state: GameState, unit: Unit, origin: tuple[int, int], cells: list, what: str) -> Email:
    tpl = state.catalog["generated"]["scorched_earth"]
    ours = state.is_friendly(unit.nation_id)
    who = f"Our retreating {unit.name} ({unit.designation})" if ours else "Retreating enemy troops"
    lines = [
        f"{who} blew up the {what} around grid {origin[0]:03d}-{origin[1]:03d} "
        f"({state.world_map.place_name(*origin)}): {len(cells)} sections are wrecked "
        "(marked x on the War Room map).",
        "",
        "Wrecked track and cratered roads give no movement or supply advantage. Armies and their supply columns "
        "must go cross-country until the line is rebuilt.",
        "",
        "RECOMMENDATION: move a Combat Engineer Battalion within 25 miles of the damage and hold it there inside "
        "our supply net. Engineers rebuild about two sections a week.",
        "",
        "— Railway Troops Directorate",
    ]
    variables = state.text_vars() | {"what": what.upper(), "x": f"{origin[0]:03d}", "y": f"{origin[1]:03d}"}
    return Email(id="scorched_earth", sender=fill(tpl["sender"], variables), subject=fill(tpl["subject"], variables),
                 classification=tpl.get("classification", "SECRET"), body="\n".join(lines))


def is_engineer(state: GameState, unit: Unit) -> bool:
    return state.role(unit) == "engineer"


def work_in_range(state: GameState, unit: Unit) -> list[tuple[int, int]]:
    reach = float(state.config.get("engineering", {}).get("repair_range", 2.5))
    return sorted((c for c in damaged_cells(state) if scaled_distance(state, c, unit.location) <= reach),
                  key=lambda c: (scaled_distance(state, c, unit.location), c))


def repair(state: GameState) -> dict[str, int]:
    """One week of engineering work. Returns cells repaired per nation."""
    per_week = int(state.config.get("engineering", {}).get("repair_per_week", 2))
    done: dict[str, int] = {}
    changed: list[tuple[int, int]] = []
    for unit in state.all_units():
        if not is_engineer(state, unit) or unit.status not in (HOLDING,) or unit.status == ENGAGED:
            continue
        if unit.supply_state != "supplied":
            continue
        for cell in work_in_range(state, unit)[:per_week]:
            set_cell(state, cell, REPAIRED[state.world_map.transport_at(*cell)])
            done[unit.nation_id] = done.get(unit.nation_id, 0) + 1
            changed.append(cell)
    if changed:
        invalidate_terrain_caches(state, changed)
    return done


class EngineeringSystem(SimulationSystem):
    """After logistics: combat engineers rebuild wrecked railways and roads."""

    name = "engineering"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        done = repair(state)
        if done.get(state.player.id):
            report.log.append(f"Engineers rebuilt {done[state.player.id]} section(s) of rail/road.")
