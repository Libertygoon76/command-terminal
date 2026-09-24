"""Logistics: the arteries of war.

Every week each formation consumes supply. It is replenished only if a supply path can be
traced from the unit back to a source:
  * a friendly city / capital / port (config logistics.source_features) in a region the
    nation owns, or
  * the nation's home map edge (Kestria: the western coast, Vosk: the eastern hinterland),
within `source_range` movement-cost units. Paths use the same costs as movement, so roads and
rail carry supply far; mountains and marsh strangle it. An HQ / Logistics formation that is
itself in supply acts as a forward depot, extending the net by `hq_range`.

Supply cannot ride an enemy's roads or railways: in territory owned by another nation the
path pays plain terrain cost times `hostile_territory_factor`. Armies may march down enemy roads,
but their supply columns cannot keep up, so deep raids become OVEREXTENDED.

Enemy zones of control (every cell within `zoc_radius` of an enemy formation) cannot be passed
THROUGH: a path may end in a ZOC cell (a unit in contact can still be fed) but never continue
out of one. Enemy-occupied cells are fully blocked.

Supply states:
  SUPPLIED      in range: consumption is offset by a delivery that shrinks with distance.
  OVEREXTENDED  a path exists but is too long: no delivery.
  ISOLATED      no path at all: cut off.
At 0% supply a formation suffers attrition (manpower and morale) and cannot advance; it may
only move to a cell inside its own supply network (fall back).
"""

from __future__ import annotations

import heapq
import math

from src.engine.event_manager import deliver
from src.engine.movement import passable, scaled_distance, step_cost
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import ENGAGED, MOVING, Email, GameState, Unit

SUPPLIED = "supplied"
OVEREXTENDED = "overextended"
ISOLATED = "isolated"


def _cfg(state: GameState) -> dict:
    return state.config.get("logistics", {})


def _enemies_of(state: GameState, nation_id: str) -> list[Unit]:
    return [u for u in state.all_units() if u.nation_id != nation_id]


def zoc_cells(state: GameState, nation_id: str) -> set[tuple[int, int]]:
    """Cells under enemy zone of control for `nation_id` (scaled distance ≤ zoc_radius rows)."""
    radius = float(_cfg(state).get("zoc_radius", 1))
    reach_x = int(radius / float(state.config.get("map", {}).get("column_scale", 0.5)))
    cells = set()
    for enemy in _enemies_of(state, nation_id):
        for dx in range(-reach_x, reach_x + 1):
            for dy in range(-int(radius), int(radius) + 1):
                cell = (enemy.x + dx, enemy.y + dy)
                if scaled_distance(state, cell, enemy.location) <= radius:
                    cells.add(cell)
    return cells


def supply_sources(state: GameState, nation_id: str) -> set[tuple[int, int]]:
    world = state.world_map
    cfg = _cfg(state)
    sources = {
        (f.x, f.y) for f in world.features
        if f.type in cfg.get("source_features", []) and world.owner_at(f.x, f.y) == nation_id
    }
    edge = cfg.get("home_edges", {}).get(nation_id)
    if edge in ("west", "east"):
        for y in range(world.height):
            owned = [x for x in range(1, world.width - 1) if world.owner_at(x, y) == nation_id]
            if owned:
                sources.add((min(owned), y) if edge == "west" else (max(owned), y))
    return {c for c in sources if passable(state, *c)}


def supply_step_cost(state: GameState, nation_id: str, frm: tuple[int, int], to: tuple[int, int]) -> float:
    owner = state.world_map.owner_at(*to)
    if owner in state.nations and owner != nation_id:  # enemy territory: no roads/rail for our supply
        return step_cost(state, frm, to, use_transport=False) * float(_cfg(state).get("hostile_territory_factor", 1.5))
    return step_cost(state, frm, to)


def entry_costs(state: GameState, nation_id: str) -> dict[tuple[int, int], tuple[float, float]]:
    """Static per-cell supply cost of entering a passable cell: (horizontally, vertically).
    The map and cost config don't change during a campaign, so this is computed once."""
    key = ("supply_entry_costs", nation_id)
    if key not in state.cost_cache:
        world = state.world_map
        costs = {}
        for y in range(world.height):
            for x in range(world.width):
                if passable(state, x, y):
                    costs[(x, y)] = (
                        supply_step_cost(state, nation_id, (x - 1, y), (x, y)),
                        supply_step_cost(state, nation_id, (x, y - 1), (x, y)),
                    )
        state.cost_cache[key] = costs
    return state.cost_cache[key]


def trace(state: GameState, nation_id: str, starts: dict[tuple[int, int], float], blocked: set, zoc: set,
          limit: float = math.inf) -> dict[tuple[int, int], float]:
    """Multi-source Dijkstra over land using supply costs. Paths may enter a ZOC cell but not
    leave it; `blocked` cells (enemy-occupied) can't be entered at all."""
    costs = entry_costs(state, nation_id)
    dist = dict(starts)
    heap = [(d, c) for c, d in starts.items()]
    heapq.heapify(heap)
    while heap:
        d, cell = heapq.heappop(heap)
        if d > dist.get(cell, math.inf) or (cell in zoc and cell not in starts):
            continue
        x, y = cell
        for nxt, axis in (((x + 1, y), 0), ((x - 1, y), 0), ((x, y + 1), 1), ((x, y - 1), 1)):
            entry = costs.get(nxt)
            if entry is None or nxt in blocked:
                continue
            nd = d + entry[axis]
            if nd <= limit and nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                heapq.heappush(heap, (nd, nxt))
    return dist


def compute_network(state: GameState, nation_id: str) -> dict:
    """Trace the nation's supply net. Stores it on state for the UI and for movement rules."""
    cfg = _cfg(state)
    source_range = float(cfg.get("source_range", 16))
    hq_range = float(cfg.get("hq_range", 10))
    zoc = zoc_cells(state, nation_id)
    blocked = {u.location for u in _enemies_of(state, nation_id)}

    primary = trace(state, nation_id, {c: 0.0 for c in supply_sources(state, nation_id) if c not in blocked}, blocked, zoc)
    templates = {t["id"]: t for t in state.catalog["units"]["units"]}
    depots = {
        u.location: 0.0 for u in state.nations[nation_id].units
        if templates[u.unit_type].get("role") == "hq" and primary.get(u.location, math.inf) <= source_range
    }
    forward = trace(state, nation_id, depots, blocked, zoc, limit=hq_range) if depots else {}

    network = {c for c, d in primary.items() if d <= source_range} | set(forward)
    info = {"primary": primary, "forward": forward, "network": network, "zoc": zoc,
            "source_range": source_range, "hq_range": hq_range}
    state.supply_networks[nation_id] = info
    return info


def supply_status(state: GameState, unit: Unit, info: dict) -> tuple[str, float]:
    """(state, reach) where reach is 0 at the source and 1 at the edge of the net."""
    d1 = info["primary"].get(unit.location, math.inf)
    d2 = info["forward"].get(unit.location, math.inf)
    if d1 <= info["source_range"] or d2 <= info["hq_range"]:
        reach = min(d1 / info["source_range"], d2 / info["hq_range"])
        return SUPPLIED, reach
    if d1 < math.inf:
        return OVEREXTENDED, 1.0
    return ISOLATED, 1.0


def update_supply(state: GameState) -> list[Unit]:
    """Weekly consumption, delivery and attrition. Returns friendly units newly ISOLATED."""
    cfg = _cfg(state)
    use = cfg.get("consumption", {})
    templates = {t["id"]: t for t in state.catalog["units"]["units"]}
    newly_isolated = []
    for nation_id, nation in state.nations.items():
        info = compute_network(state, nation_id)
        for unit in nation.units:
            status, reach = supply_status(state, unit, info)
            if status == ISOLATED and unit.supply_state != ISOLATED and state.is_friendly(nation_id):
                newly_isolated.append(unit)
            unit.supply_state = status
            consumed = float(use.get("base", 8))
            if unit.status == MOVING:
                consumed += float(use.get("moving", 4))
            if unit.status == ENGAGED:
                consumed += float(use.get("engaged", 12))
            delivered = 0.0
            if status == SUPPLIED:
                hi, lo = float(cfg.get("delivery_max", 35)), float(cfg.get("delivery_min", 15))
                delivered = hi - (hi - lo) * reach
            unit.supply = max(0.0, min(100.0, unit.supply - consumed + delivered))
            if unit.supply <= 0:
                floor = int(templates[unit.unit_type]["manpower"] * float(cfg.get("min_strength_fraction", 0.1)))
                loss = max(1, round(unit.strength * float(cfg.get("attrition_rate", 0.04))))
                unit.strength = max(floor, unit.strength - loss)
                unit.morale = max(0.0, unit.morale - float(cfg.get("attrition_morale", 5)))
    return newly_isolated


def player_supply_picture(state: GameState) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """What the Kestrian staff can know about its own supply net: (network, enemy ZOC).

    The simulation uses true enemy positions, but the map overlay must not leak hidden units:
    ZOC is drawn only around observed hostiles, and the holes that hidden units punch in our
    net are painted over (we only learn of those when convoys stop getting through).
    """
    info = state.supply_networks.get(state.player.id)
    if not info:
        return set(), set()
    visible = state.visible_hostiles()
    radius = float(_cfg(state).get("zoc_radius", 1))
    known_zoc = {c for c in info["zoc"] if any(scaled_distance(state, c, u.location) <= radius for u in visible)}
    network = set(info["network"])
    hidden = (info["zoc"] - known_zoc) - network
    steps = ((1, 0), (-1, 0), (0, 1), (0, -1))
    frontier = [c for c in hidden if any((c[0] + dx, c[1] + dy) in network for dx, dy in steps)]
    filled = set(frontier)
    while frontier:  # flood-fill every hidden-ZOC hole that touches our net
        x, y = frontier.pop()
        for dx, dy in steps:
            nxt = (x + dx, y + dy)
            if nxt in hidden and nxt not in filled:
                filled.add(nxt)
                frontier.append(nxt)
    return (network | filled) - known_zoc, known_zoc


def can_advance(state: GameState, unit: Unit) -> bool:
    """A formation at 0% supply may only fall back into its own supply network."""
    if unit.supply > 0 or unit.active_order is None:
        return True
    info = state.supply_networks.get(unit.nation_id)
    return bool(info) and unit.active_order.target in info["network"]


def speed_factor(state: GameState, unit: Unit) -> float:
    cfg = _cfg(state)
    if unit.supply < float(cfg.get("low_supply_threshold", 25)):
        return float(cfg.get("low_supply_speed_factor", 0.5))
    return 1.0


def isolation_email(state: GameState, unit: Unit) -> Email:
    tpl = state.catalog["generated"]["isolated"]
    region = state.world_map.region_at(*unit.location)
    variables = state.text_vars() | {
        "unit": unit.name,
        "designation": unit.designation,
        "x": f"{unit.x:03d}",
        "y": f"{unit.y:03d}",
        "sector": region.name if region else "open ground",
        "supply": f"{unit.supply:.0f}",
    }
    return Email(
        id="supply_isolated",
        sender=fill(tpl["sender"], variables),
        subject=fill(tpl["subject"], variables),
        classification=tpl.get("classification", "SECRET"),
        body=fill(tpl["body"], variables),
    )


class LogisticsEngine(SimulationSystem):
    """Runs after movement and recon: traces supply for every nation and applies attrition."""

    name = "logistics"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        for unit in update_supply(state):
            report.new_messages.append(deliver(state, isolation_email(state, unit)))
            report.log.append(f"{unit.designation} ISOLATED.")
        starving = [u.designation for u in state.player.units if u.supply <= 0]
        if starving:
            report.log.append(f"Out of supply: {', '.join(starving)}.")
