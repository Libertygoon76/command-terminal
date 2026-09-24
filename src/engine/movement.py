"""Unit movement: route planning, orders, weekly movement resolution, and skirmish detection.

Geometry: units move between 4-neighbouring cells. A column is half as far as a row
(config map.column_scale = 0.5), so a horizontal step costs half a vertical one.
Step cost:
  * along a rail / road / destroyed-rail cell (world.json `transport`): distance x
    map.transport_cost[kind], regardless of terrain (the fast arteries);
  * otherwise distance / terrain movement multiplier (mountains 0.4, marsh 0.5 ...), times
    map.obstacle_cost for trench lines.
`move_speed` (units.json) is movement points per week; unspent points carry over, so slow units
still crawl through mountains. Low supply halves speed; at 0% supply a unit may only fall back
into its own supply network (see logistics_engine).

Resolution is simultaneous: every moving unit advances one cell per round, in lockstep,
until nobody can afford their next step. After each round we check for NEW contact:
  * a hostile unit in the same cell or adjacent to it (scaled distance ≤ 1: one row, or up to
    two columns, since a column is half a row), or
  * two hostile units that swapped cells in the same round (crossed paths).
Both sides then halt and become ENGAGED, and a CRITICAL: BORDER CLASH dispatch is sent.
Units already engaged with each other may move without being re-halted (to break contact).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from src.engine.event_manager import GameOverError, deliver
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import ENGAGED, HOLDING, MOVING, Email, GameState, MoveOrder, Unit


class OrderError(ValueError):
    """An order that cannot be issued (wrong owner, unreachable target, ...)."""


@dataclass(frozen=True)
class Route:
    path: list[tuple[int, int]]  # cells to enter, in order (excludes the start cell)
    cost: float
    eta_weeks: int


# --- terrain costs ------------------------------------------------------------


def _template(state: GameState, unit: Unit) -> dict:
    return next(t for t in state.catalog["units"]["units"] if t["id"] == unit.unit_type)


def speed_of(state: GameState, unit: Unit) -> float:
    return float(_template(state, unit).get("move_speed", 4))


def passable(state: GameState, x: int, y: int) -> bool:
    world = state.world_map
    # Symbols are 3 cells wide, so units keep one column clear of the map edge.
    return 1 <= x <= world.width - 2 and 0 <= y < world.height and not world.is_sea(x, y)


def step_cost(state: GameState, frm: tuple[int, int], to: tuple[int, int], *, use_transport: bool = True) -> float:
    world = state.world_map
    cfg = state.config.get("map", {})
    distance = float(cfg.get("column_scale", 0.5)) if frm[1] == to[1] else 1.0
    kind = world.transport_at(*to)
    transport = cfg.get("transport_cost", {})
    if use_transport and kind in transport:
        return distance * float(transport[kind])
    region = world.region_at(*to)
    movement = float(world.terrain_info(region.terrain).get("movement", 1.0)) if region else 1.0
    cost = distance / max(movement, 0.1)
    if kind in cfg.get("obstacle_cost", {}):
        cost *= float(cfg["obstacle_cost"][kind])
    return cost


def _min_step_factor(state: GameState) -> float:
    return min([1.0, *[float(v) for v in state.config.get("map", {}).get("transport_cost", {}).values()]])


def plan_route(state: GameState, unit: Unit, target: tuple[int, int]) -> Route | None:
    """Cheapest 4-neighbour path (A*) from the unit to `target`, or None if unreachable."""
    start = unit.location
    if not passable(state, *target):
        return None
    if start == target:
        return Route([], 0.0, 0)
    col = float(state.config.get("map", {}).get("column_scale", 0.5))
    min_factor = _min_step_factor(state)

    def h(cell: tuple[int, int]) -> float:  # admissible: cheapest possible terrain everywhere
        return (abs(cell[0] - target[0]) * col + abs(cell[1] - target[1])) * min_factor

    frontier = [(h(start), 0.0, start)]
    came: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    best = {start: 0.0}
    while frontier:
        _, g, cell = heapq.heappop(frontier)
        if cell == target:
            break
        if g > best.get(cell, math.inf):
            continue
        x, y = cell
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not passable(state, *nxt):
                continue
            ng = g + step_cost(state, cell, nxt)
            if ng < best.get(nxt, math.inf):
                best[nxt] = ng
                came[nxt] = cell
                heapq.heappush(frontier, (ng + h(nxt), ng, nxt))
    if target not in came:
        return None
    path = []
    cell = target
    while cell != start:
        path.append(cell)
        cell = came[cell]
    path.reverse()
    cost = best[target]
    from src.engine.logistics_engine import speed_factor

    speed = speed_of(state, unit) * speed_factor(state, unit)
    eta = max(1, math.ceil(max(0.0, cost - unit.move_points) / speed)) if path else 0
    return Route(path, cost, eta)


# --- orders -------------------------------------------------------------------


def issue_move_order(state: GameState, unit_id: str, target: tuple[int, int], *, nation_id: str | None = None) -> Route:
    """Order a unit to march to `target`. `nation_id` is who is giving the order (default: player)."""
    if state.game_over:
        raise GameOverError("The government has fallen. The terminal is locked.")
    unit = state.unit(unit_id)
    if unit is None:
        raise OrderError(f"No such formation: {unit_id}")
    issuer = nation_id or state.player.id
    if unit.nation_id != issuer:
        raise OrderError(f"{unit.designation} is not under your command.")
    target = (int(target[0]), int(target[1]))
    if not state.world_map.in_bounds(*target):
        raise OrderError(f"Grid {target[0]:03d}-{target[1]:03d} is off the map.")
    if state.world_map.is_sea(*target):
        raise OrderError(f"Grid {target[0]:03d}-{target[1]:03d} is open water.")
    route = plan_route(state, unit, target)
    if route is None:
        raise OrderError(f"No land route for {unit.designation} to grid {target[0]:03d}-{target[1]:03d}.")
    if not route.path:
        cancel_order(state, unit_id, nation_id=issuer)
        return route
    unit.active_order = MoveOrder(target=target, issued_turn=state.clock.turn)
    if not unit.engaged:
        unit.status = MOVING
    return route


def cancel_order(state: GameState, unit_id: str, *, nation_id: str | None = None) -> None:
    unit = state.unit(unit_id)
    if unit is None or unit.nation_id != (nation_id or state.player.id):
        raise OrderError(f"No such formation under your command: {unit_id}")
    unit.active_order = None
    unit.move_points = 0.0
    if not unit.engaged:
        unit.status = HOLDING


# --- weekly resolution --------------------------------------------------------


def scaled_distance(state: GameState, a: tuple[int, int], b: tuple[int, int]) -> float:
    """Map distance in rows: a column counts as map.column_scale of a row."""
    col = float(state.config.get("map", {}).get("column_scale", 0.5))
    return abs(a[0] - b[0]) * col + abs(a[1] - b[1])


def in_contact(state: GameState, a: Unit, b: Unit) -> bool:
    return scaled_distance(state, a.location, b.location) <= 1.0


def _hostile(state: GameState, a: Unit, b: Unit) -> bool:
    return a.nation_id != b.nation_id and (state.is_friendly(a.nation_id) or state.is_friendly(b.nation_id))


def _engage(state: GameState, a: Unit, b: Unit, new_pairs: list[tuple[Unit, Unit]]) -> None:
    pair = frozenset((a.id, b.id))
    if pair in state.engagements:
        return  # already in contact: don't re-halt a unit that is trying to break away
    for unit, other in ((a, b), (b, a)):
        unit.status = ENGAGED
        unit.active_order = None
        unit.move_points = 0.0
        if other.id not in unit.engaged_with:
            unit.engaged_with.append(other.id)
    state.engagements.add(pair)
    new_pairs.append((a, b) if state.is_friendly(a.nation_id) else (b, a))


def resolve_movement(state: GameState) -> list[tuple[Unit, Unit]]:
    """Advance all ordered units for one week. Returns newly engaged (friendly, hostile) pairs."""
    from src.engine.logistics_engine import can_advance, speed_factor

    units = state.all_units()
    new_pairs: list[tuple[Unit, Unit]] = []
    routes: dict[str, list[tuple[int, int]]] = {}

    for unit in units:
        if unit.active_order is None:
            continue
        route = plan_route(state, unit, unit.active_order.target)
        if route is None or not route.path:
            unit.active_order = None
            unit.move_points = 0.0
            if not unit.engaged:
                unit.status = HOLDING
            continue
        if not can_advance(state, unit):
            unit.move_points = 0.0  # out of supply: halted until ordered back into the supply net
            continue
        unit.move_points += speed_of(state, unit) * speed_factor(state, unit)
        routes[unit.id] = list(route.path)
        if unit.engaged:  # moving away from the enemy breaks contact next check
            unit.status = MOVING

    movers = sorted((u for u in units if u.id in routes), key=lambda u: u.id)
    while True:
        moved: dict[str, tuple[tuple[int, int], tuple[int, int]]] = {}
        for unit in movers:
            path = routes.get(unit.id)
            if not path or unit.active_order is None:
                continue
            cost = step_cost(state, unit.location, path[0])
            if unit.move_points + 1e-9 < cost:
                continue
            unit.move_points -= cost
            moved[unit.id] = (unit.location, path[0])
            unit.location = path.pop(0)
        if not moved:
            break
        # Contact checks after the round: shared cells, then swapped cells (crossed paths).
        for mover_id, (frm, to) in moved.items():
            mover = state.unit(mover_id)
            for other in units:
                if other.id == mover_id or not _hostile(state, mover, other):
                    continue
                crossed = other.id in moved and moved[other.id] == (to, frm)
                if in_contact(state, mover, other) or crossed:
                    _engage(state, mover, other, new_pairs)

    for unit in units:
        if unit.id in routes and unit.active_order is not None and not routes[unit.id]:
            unit.active_order = None  # arrived
            unit.move_points = 0.0
            unit.status = HOLDING
    _refresh_engagements(state)
    return new_pairs


def _refresh_engagements(state: GameState) -> None:
    """Contact persists while hostile units stay in contact range of each other."""
    units = {u.id: u for u in state.all_units()}
    for pair in list(state.engagements):
        a, b = (units[i] for i in pair)
        if not in_contact(state, a, b):
            state.engagements.discard(pair)
            for unit, other in ((a, b), (b, a)):
                if other.id in unit.engaged_with:
                    unit.engaged_with.remove(other.id)
    for unit in units.values():
        if unit.status == ENGAGED and not unit.engaged_with:
            unit.status = MOVING if unit.active_order else HOLDING
        elif unit.engaged_with:
            unit.status = ENGAGED


# --- tick system --------------------------------------------------------------


def clash_email(state: GameState, friendly: Unit, hostile: Unit) -> Email:
    from src.engine.map_overlay import hostile_label  # avoid import cycle

    tpl = state.catalog["generated"]["clash"]
    region = state.world_map.region_at(*friendly.location)
    variables = state.text_vars() | {
        "friendly": friendly.name,
        "friendly_designation": friendly.designation,
        "hostile_desc": hostile_label(state, hostile),
        "x": f"{hostile.x:03d}",
        "y": f"{hostile.y:03d}",
        "sector": region.name if region else "open ground",
    }
    return Email(
        id="border_clash",
        sender=fill(tpl["sender"], variables),
        subject=fill(tpl["subject"], variables),
        classification=tpl.get("classification", "TOP SECRET"),
        body=fill(tpl["body"], variables),
    )


class MovementSystem(SimulationSystem):
    """Resolves the week's marches for every nation, then reports new border clashes."""

    name = "movement"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        new_pairs = resolve_movement(state)
        if new_pairs:
            from src.engine.recon import update_contacts  # clashes reveal the enemy immediately

            update_contacts(state)
        for friendly, hostile in new_pairs:
            report.new_messages.append(deliver(state, clash_email(state, friendly, hostile)))
            report.log.append(f"BORDER CLASH at grid {hostile.x:03d}-{hostile.y:03d}.")
