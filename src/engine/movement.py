"""Unit movement: route planning, orders, weekly movement resolution, and skirmish detection.

SCALE: one map row = 10 miles (tick_engine.MILES_PER_CELL); a column is half as far as a row
(config map.column_scale = 0.5), so a horizontal step costs half a vertical one.

Speed is authored in MILES PER DAY (units.json `speed_mpd`, plus `truck_mpd` scaled by how many of
its trucks the formation has and whether it has fuel). Movement points per week = miles per week /
10. An infantry division marching on foot covers 4 miles a day across open country: 2.8 rows a
week. With every truck fuelled it makes 7 miles a day; on a road (map.transport_cost 0.55) nearly
twice that; by rail (0.35) about three times.

Step cost for LAND formations:
  * along a rail / road / destroyed-rail cell (world.json `transport`): distance x
    map.transport_cost[kind], regardless of terrain (the fast arteries);
  * otherwise distance / terrain movement multiplier (mountains 0.4, marsh 0.5 ...), times
    map.obstacle_cost for trench lines.
WARSHIPS (domain "sea") move only on open water, at plain distance.
Unspent points carry over, so slow units still crawl through mountains. Low supply halves speed,
the weather multiplies it (mud season, snow); at 0% supply a unit may only fall back into its own
supply network (see logistics_engine).

Routes are cached per (domain, start, target): costs never change during a campaign, and the suffix of
an optimal route is itself optimal, so a marching column never needs a fresh search.

Resolution is simultaneous: every moving unit advances one cell per round, in lockstep,
until nobody can afford their next step. After each round we check for NEW contact between hostile
formations of the SAME domain (armies meet armies, fleets meet fleets):
  * a hostile unit in the same cell or adjacent to it (scaled distance ≤ 1), or
  * two hostile units that swapped cells in the same round (crossed paths).
Both sides then halt and become ENGAGED, and a CRITICAL: BORDER CLASH (or NAVAL CONTACT) dispatch is sent.
Units already engaged with each other may move without being re-halted (to break contact).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from src.engine.event_manager import GameOverError, deliver
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.engine.tick_engine import cells_per_turn
from src.models import ENGAGED, HOLDING, MOVING, Email, GameState, MoveOrder, Unit

LAND = "land"
SEA = "sea"
ROUTE_CACHE_LIMIT = 6000


class OrderError(ValueError):
    """An order that cannot be issued (wrong owner, unreachable target, ...)."""


@dataclass(frozen=True)
class Route:
    path: list[tuple[int, int]]  # cells to enter, in order (excludes the start cell)
    cost: float
    eta_weeks: int


# --- terrain costs ------------------------------------------------------------


def _template(state: GameState, unit: Unit) -> dict:
    return state.template(unit.unit_type)


def pace_mpd(state: GameState, unit: Unit) -> float:
    """Sustained pace in miles per day: foot/track pace plus the truck bonus (needs trucks and fuel)."""
    template = _template(state, unit)
    pace = float(template.get("speed_mpd", 4))
    bonus = float(template.get("truck_mpd", 0))
    if bonus and unit.equipment_inventory.get("fuel_drums", 0) > 0:
        from src.engine.logistics_engine import establishment  # avoid import cycle

        wanted = establishment(state, unit).get("truck_4t", 0)
        if wanted:
            pace += bonus * min(1.0, unit.equipment_inventory.get("truck_4t", 0) / wanted)
    return pace


def speed_of(state: GameState, unit: Unit) -> float:
    """Movement points per week (rows of open plains) before supply and weather factors."""
    return cells_per_turn(state, pace_mpd(state, unit))


def passable(state: GameState, x: int, y: int, domain: str = LAND) -> bool:
    world = state.world_map
    # Symbols are 3 cells wide, so units keep one column clear of the map edge.
    if not (1 <= x <= world.width - 2 and 0 <= y < world.height):
        return False
    return world.is_sea(x, y) if domain == SEA else not world.is_sea(x, y)


def step_cost(state: GameState, frm: tuple[int, int], to: tuple[int, int], *, use_transport: bool = True,
              domain: str = LAND) -> float:
    world = state.world_map
    cfg = state.config.get("map", {})
    distance = float(cfg.get("column_scale", 0.5)) if frm[1] == to[1] else 1.0
    if domain == SEA:
        return distance
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


def _min_step_factor(state: GameState, domain: str) -> float:
    if domain == SEA:
        return 1.0
    return min([1.0, *[float(v) for v in state.config.get("map", {}).get("transport_cost", {}).values()]])


def move_costs(state: GameState, domain: str) -> dict[tuple[int, int], tuple[float, float]]:
    """Static cost of entering every passable cell: (horizontally, vertically). Computed once."""
    key = ("move_costs", domain)
    if key not in state.cost_cache:
        world = state.world_map
        costs = {}
        for y in range(world.height):
            for x in range(world.width):
                if passable(state, x, y, domain):
                    costs[(x, y)] = (step_cost(state, (x - 1, y), (x, y), domain=domain),
                                     step_cost(state, (x, y - 1), (x, y), domain=domain))
        state.cost_cache[key] = costs
    return state.cost_cache[key]


def find_path(state: GameState, domain: str, start: tuple[int, int],
              target: tuple[int, int]) -> tuple[tuple[tuple[int, int], ...], float] | None:
    """Cheapest 4-neighbour path (A*), cached. Returns (cells to enter, cost) or None."""
    cache = state.cost_cache.setdefault("routes", {})
    key = (domain, start, target)
    if key in cache:
        return cache[key]
    costs = move_costs(state, domain)
    result = None
    if target in costs:
        col = float(state.config.get("map", {}).get("column_scale", 0.5))
        min_factor = _min_step_factor(state, domain)
        tx, ty = target

        def h(cell: tuple[int, int]) -> float:  # admissible: cheapest possible terrain everywhere
            return (abs(cell[0] - tx) * col + abs(cell[1] - ty)) * min_factor

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
            for nxt, axis in (((x + 1, y), 0), ((x - 1, y), 0), ((x, y + 1), 1), ((x, y - 1), 1)):
                entry = costs.get(nxt)
                if entry is None:
                    continue
                ng = g + entry[axis]
                if ng < best.get(nxt, math.inf):
                    best[nxt] = ng
                    came[nxt] = cell
                    heapq.heappush(frontier, (ng + h(nxt), ng, nxt))
        if target in came:
            path = []
            cell = target
            while cell != start:
                path.append(cell)
                cell = came[cell]
            path.reverse()
            result = (tuple(path), best[target])
    if len(cache) > ROUTE_CACHE_LIMIT:
        cache.clear()
    cache[key] = result
    return result


def plan_route(state: GameState, unit: Unit, target: tuple[int, int]) -> Route | None:
    """Cheapest path from the unit to `target` over its own element (land or sea), or None."""
    domain = state.domain(unit)
    start = unit.location
    if not passable(state, *target, domain):
        return None
    if start == target:
        return Route([], 0.0, 0)
    found = find_path(state, domain, start, target)
    if found is None:
        return None
    path, cost = found
    from src.engine.logistics_engine import speed_factor

    speed = max(0.05, speed_of(state, unit) * speed_factor(state, unit))
    eta = max(1, math.ceil(max(0.0, cost - unit.move_points) / speed)) if path else 0
    return Route(list(path), cost, eta)


# --- orders -------------------------------------------------------------------


def issue_move_order(state: GameState, unit_id: str, target: tuple[int, int], *, nation_id: str | None = None) -> Route:
    """Order a unit to march (or sail) to `target`. `nation_id` is who is giving the order (default: player)."""
    if state.game_over:
        raise GameOverError("The government has fallen. The terminal is locked.")
    unit = state.unit(unit_id)
    if unit is None:
        raise OrderError(f"No such formation: {unit_id}")
    issuer = nation_id or state.player.id
    if unit.nation_id != issuer:
        raise OrderError(f"{unit.designation} is not under your command.")
    if unit.routing:
        raise OrderError(f"{unit.designation} is routing and will not answer orders for {unit.routing_weeks} week(s).")
    target = (int(target[0]), int(target[1]))
    grid = f"{target[0]:03d}-{target[1]:03d}"
    if not state.world_map.in_bounds(*target):
        raise OrderError(f"Grid {grid} is off the map.")
    naval = state.domain(unit) == SEA
    if naval and not state.world_map.is_sea(*target):
        raise OrderError(f"Grid {grid} is on land. Warships sail only on open water.")
    if not naval and state.world_map.is_sea(*target):
        raise OrderError(f"Grid {grid} is open water.")
    route = plan_route(state, unit, target)
    if route is None:
        raise OrderError(f"No {'sea' if naval else 'land'} route for {unit.designation} to grid {grid}.")
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
    """Map distance in rows (10 miles each): a column counts as map.column_scale of a row."""
    col = float(state.config.get("map", {}).get("column_scale", 0.5))
    return abs(a[0] - b[0]) * col + abs(a[1] - b[1])


def in_contact(state: GameState, a: Unit, b: Unit) -> bool:
    return scaled_distance(state, a.location, b.location) <= 1.0


def hostile(state: GameState, a: Unit, b: Unit) -> bool:
    """Enemies that can fight each other: different sides, and both on land or both at sea."""
    return (a.nation_id != b.nation_id and (state.is_friendly(a.nation_id) or state.is_friendly(b.nation_id))
            and state.domain(a) == state.domain(b))


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
    domains = {u.id: state.domain(u) for u in units}

    for unit in units:
        if unit.active_order is None or unit.routing:
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
            cost = step_cost(state, unit.location, path[0], domain=domains[unit.id])
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
                if other.id == mover_id or not hostile(state, mover, other):
                    continue
                crossed = other.id in moved and moved[other.id] == (to, frm)
                if in_contact(state, mover, other) or crossed:
                    _engage(state, mover, other, new_pairs)

    for unit in units:
        if unit.id in routes and unit.active_order is not None and not routes[unit.id]:
            unit.active_order = None  # arrived
            unit.move_points = 0.0
            unit.status = HOLDING
    refresh_engagements(state)
    return new_pairs


def refresh_engagements(state: GameState) -> None:
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
        if unit.routing:
            continue
        if unit.status == ENGAGED and not unit.engaged_with:
            unit.status = MOVING if unit.active_order else HOLDING
        elif unit.engaged_with:
            unit.status = ENGAGED


# --- tick system --------------------------------------------------------------


def clash_email(state: GameState, friendly: Unit, hostile_unit: Unit) -> Email:
    from src.engine.map_overlay import hostile_label  # avoid import cycle

    naval = state.domain(friendly) == SEA
    tpl = state.catalog["generated"]["naval_contact" if naval else "clash"]
    variables = state.text_vars() | {
        "friendly": friendly.name,
        "friendly_designation": friendly.designation,
        "hostile_desc": hostile_label(state, hostile_unit),
        "x": f"{hostile_unit.x:03d}",
        "y": f"{hostile_unit.y:03d}",
        "sector": state.world_map.place_name(*friendly.location),
    }
    return Email(
        id="naval_contact" if naval else "border_clash",
        sender=fill(tpl["sender"], variables),
        subject=fill(tpl["subject"], variables),
        classification=tpl.get("classification", "TOP SECRET"),
        body=fill(tpl["body"], variables),
    )


class MovementSystem(SimulationSystem):
    """Resolves the week's marches and voyages for every nation, then reports new contacts."""

    name = "movement"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        new_pairs = resolve_movement(state)
        if new_pairs:
            from src.engine.recon import update_contacts  # clashes reveal the enemy immediately

            update_contacts(state)
        for friendly, hostile_unit in new_pairs:
            report.new_messages.append(deliver(state, clash_email(state, friendly, hostile_unit)))
            what = "NAVAL CONTACT" if state.domain(friendly) == SEA else "BORDER CLASH"
            report.log.append(f"{what} at grid {hostile_unit.x:03d}-{hostile_unit.y:03d}.")
