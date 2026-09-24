"""Logistics: the arteries of war.

Every week each formation consumes supply. It is replenished only if a supply path can be
traced from the unit back to a source:
  * a friendly settlement (config logistics.source_features) in a region the nation owns. Each
    type reaches its own distance (source_range_by_type): capitals, cities, industrial centres
    and ports feed the whole theatre, a market town only its neighbourhood. A BLOCKADED port
    supplies nothing (see naval_engine);
  * the nation's home map edge (the Vosk hinterland beyond the eastern edge of the sheet),
within `source_range` movement-cost units. Paths use the same costs as movement, so roads and
rail carry supply far; mountains and marsh strangle it. An HQ / Logistics formation that is
itself in supply acts as a forward depot, extending the net by `hq_range`.

Supply cannot ride an enemy's roads or railways: in territory owned by another nation the
path pays plain terrain cost times `hostile_territory_factor`. Armies may march down enemy roads,
but their supply columns cannot keep up, so deep raids become OVEREXTENDED.

Enemy zones of control (every cell within `zoc_radius` of an enemy LAND formation) cannot be passed
THROUGH: a path may end in a ZOC cell (a unit in contact can still be fed) but never continue
out of one. Enemy-occupied cells are fully blocked. Warships never cut land supply lines.

WARSHIPS are supplied by sea: SUPPLIED within naval.supply_range (sea distance) of a friendly,
unblockaded harbour, otherwise OVEREXTENDED (at sea, living on what they carry).

Supply states:
  SUPPLIED      in range: consumption is offset by a delivery that shrinks with distance.
  OVEREXTENDED  a path exists but is too long: no delivery.
  ISOLATED      no path at all: cut off.
At 0% supply a formation suffers attrition (manpower and morale) and cannot advance; it may
only move to a cell inside its own supply network (fall back).

Equipment pipeline: the generic supply % stands for food, spares and general stores. Weapons,
ammunition and fuel are PHYSICAL: each formation carries an equipment_inventory, burns fuel every week
(template `fuel_use` by status; armor burns weather.armor_fuel times as much while moving in the mud)
and ammunition in combat. SUPPLIED formations draw from the nation's national_stockpile toward their
establishment (template loadout scaled by current strength, plus researched upgrades; `modernize`
swaps old kit for new, and superseded stock is turned in to the depots as the new arrives). Engaged
formations are served first; delivery per week is capped by the length of the supply line (config
logistics.resupply). An empty depot delivers nothing. Supplied formations out of combat also receive
replacement troops from the manpower pool and slowly recover morale.
"""

from __future__ import annotations

import heapq
import math

from src.engine.event_manager import deliver
from src.engine.movement import LAND, SEA, move_costs, passable, scaled_distance, step_cost
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import ENGAGED, MOVING, ROUTING, Email, GameState, Nation, Unit

SUPPLIED = "supplied"
OVEREXTENDED = "overextended"
ISOLATED = "isolated"
WEAPON_CATEGORIES = ("small_arms", "armor", "artillery", "warship")


def _cfg(state: GameState) -> dict:
    return state.config.get("logistics", {})


def _land_enemies_of(state: GameState, nation_id: str) -> list[Unit]:
    return [u for u in state.all_units() if u.nation_id != nation_id and state.domain(u) == LAND]


def zoc_cells(state: GameState, nation_id: str) -> set[tuple[int, int]]:
    """Cells under enemy zone of control for `nation_id` (scaled distance ≤ zoc_radius rows)."""
    radius = float(_cfg(state).get("zoc_radius", 1))
    reach_x = int(radius / float(state.config.get("map", {}).get("column_scale", 0.5)))
    cells = set()
    for enemy in _land_enemies_of(state, nation_id):
        for dx in range(-reach_x, reach_x + 1):
            for dy in range(-int(radius), int(radius) + 1):
                cell = (enemy.x + dx, enemy.y + dy)
                if scaled_distance(state, cell, enemy.location) <= radius:
                    cells.add(cell)
    return cells


def supply_sources(state: GameState, nation_id: str) -> dict[tuple[int, int], float]:
    """Source cell -> starting cost. A source that reaches less than `source_range` starts part-way
    along (source_range - its own reach). Blockaded ports are left out."""
    key = ("supply_sources", nation_id)
    if key not in state.cost_cache:
        state.cost_cache[key] = _find_sources(state, nation_id)
    blockaded = {name for name, holder in state.blockades.items() if holder != nation_id}
    return {cell: d for cell, (d, name) in state.cost_cache[key].items() if name not in blockaded}


def _find_sources(state: GameState, nation_id: str) -> dict[tuple[int, int], tuple[float, str]]:
    world = state.world_map
    cfg = _cfg(state)
    full = float(cfg.get("source_range", 16))
    ranges = cfg.get("source_range_by_type", {})
    sources: dict[tuple[int, int], tuple[float, str]] = {}
    for f in world.features:
        if f.type in cfg.get("source_features", []) and world.owner_at(f.x, f.y) == nation_id:
            reach = min(full, float(ranges.get(f.type, full)))
            sources[(f.x, f.y)] = (full - reach, f.name)
    edge = cfg.get("home_edges", {}).get(nation_id)
    if edge in ("west", "east"):
        for y in range(world.height):
            owned = [x for x in range(1, world.width - 1) if world.owner_at(x, y) == nation_id]
            if owned:
                cell = (min(owned), y) if edge == "west" else (max(owned), y)
                sources.setdefault(cell, (0.0, ""))
    return {c: v for c, v in sources.items() if passable(state, *c)}


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


def _cost_arrays(state: GameState, key: tuple, costs: dict) -> tuple[list[float], list[float]]:
    """Flatten a {cell: (h, v)} entry-cost table into two lists indexed y * width + x (inf = impassable)."""
    cache_key = ("cost_arrays", key)
    if cache_key not in state.cost_cache:
        width, height = state.world_map.width, state.world_map.height
        hc = [math.inf] * (width * height)
        vc = [math.inf] * (width * height)
        for (x, y), (h, v) in costs.items():
            hc[y * width + x] = h
            vc[y * width + x] = v
        state.cost_cache[cache_key] = (hc, vc)
    return state.cost_cache[cache_key]


def trace(state: GameState, nation_id: str, starts: dict[tuple[int, int], float], blocked: set, zoc: set,
          limit: float = math.inf, costs: dict | None = None, costs_key: tuple | None = None) -> dict[tuple[int, int], float]:
    """Multi-source Dijkstra using supply costs. Paths may enter a ZOC cell but not
    leave it; `blocked` cells (enemy-occupied) can't be entered at all.

    Runs on flat arrays (the map is 19,200 cells and this is traced for every nation every week)."""
    if costs is None:
        costs, costs_key = entry_costs(state, nation_id), ("supply", nation_id)
    hc, vc = _cost_arrays(state, costs_key or ("adhoc", id(costs)), costs)
    width = state.world_map.width
    size = len(hc)
    inf = math.inf
    dist = [inf] * size
    blocked_i = {y * width + x for x, y in blocked}
    zoc_i = {y * width + x for x, y in zoc}
    start_i = set()
    heap = []
    for (x, y), d in starts.items():
        i = y * width + x
        start_i.add(i)
        if d < dist[i]:
            dist[i] = d
            heap.append((d, i))
    heapq.heapify(heap)
    touched = list(start_i)
    pop, push = heapq.heappop, heapq.heappush
    while heap:
        d, i = pop(heap)
        if d > dist[i] or (i in zoc_i and i not in start_i):
            continue
        x = i % width
        for j, c in ((i + 1, hc[i + 1] if x + 1 < width else inf), (i - 1, hc[i - 1] if x > 0 else inf),
                     (i + width, vc[i + width] if i + width < size else inf), (i - width, vc[i - width] if i >= width else inf)):
            if c == inf or j in blocked_i:
                continue
            nd = d + c
            if nd <= limit and nd < dist[j]:
                if dist[j] == inf:
                    touched.append(j)
                dist[j] = nd
                push(heap, (nd, j))
    return {(i % width, i // width): dist[i] for i in touched if dist[i] < inf}


def harbours(state: GameState, nation_id: str) -> dict[tuple[int, int], str]:
    """Harbour cell -> port name for the nation's ports that are not under blockade."""
    world = state.world_map
    return {p.harbour: p.name for p in world.ports()
            if p.harbour and world.owner_at(p.x, p.y) == nation_id and p.name not in state.blockades}


def compute_network(state: GameState, nation_id: str) -> dict:
    """Trace the nation's land and sea supply nets. Stored on state for the UI and movement rules."""
    cfg = _cfg(state)
    source_range = float(cfg.get("source_range", 16))
    hq_range = float(cfg.get("hq_range", 10))
    zoc = zoc_cells(state, nation_id)
    blocked = {u.location for u in _land_enemies_of(state, nation_id)}

    starts = {c: d for c, d in supply_sources(state, nation_id).items() if c not in blocked}
    primary = trace(state, nation_id, starts, blocked, zoc, limit=source_range)
    depots = {
        u.location: 0.0 for u in state.nations[nation_id].units
        if state.role(u) == "hq" and primary.get(u.location, math.inf) <= source_range
    }
    forward = trace(state, nation_id, depots, blocked, zoc, limit=hq_range) if depots else {}

    naval_range = float(state.config.get("naval", {}).get("supply_range", 45))
    ports = harbours(state, nation_id)
    sea_key = ("sea_net", nation_id, frozenset(ports))  # static until a blockade opens or closes a harbour
    if sea_key not in state.cost_cache:
        state.cost_cache[sea_key] = trace(state, nation_id, {c: 0.0 for c in ports}, set(), set(), limit=naval_range,
                                          costs=move_costs(state, SEA), costs_key=("move", SEA)) if ports else {}
    sea = state.cost_cache[sea_key]

    network = {c for c, d in primary.items() if d <= source_range} | set(forward)
    info = {"primary": primary, "forward": forward, "network": network, "zoc": zoc,
            "source_range": source_range, "hq_range": hq_range, "sea": sea, "sea_range": naval_range,
            "starts": set(starts), "blocked": blocked, "connected": None}
    state.supply_networks[nation_id] = info
    return info


def supply_status(state: GameState, unit: Unit, info: dict) -> tuple[str, float]:
    """(state, reach) where reach is 0 at the source and 1 at the edge of the net."""
    if state.domain(unit) == SEA:
        d = info.get("sea", {}).get(unit.location, math.inf)
        if d <= info.get("sea_range", 45):
            return SUPPLIED, d / max(1.0, info.get("sea_range", 45))
        return OVEREXTENDED, 1.0
    d1 = info["primary"].get(unit.location, math.inf)
    d2 = info["forward"].get(unit.location, math.inf)
    if d1 <= info["source_range"] or d2 <= info["hq_range"]:
        reach = min(d1 / info["source_range"], d2 / info["hq_range"])
        return SUPPLIED, reach
    if unit.location in _connected(state, info):
        return OVEREXTENDED, 1.0
    return ISOLATED, 1.0


def _connected(state: GameState, info: dict) -> set[tuple[int, int]]:
    """Every cell any supply path could reach at all (ignoring range): a cheap flood fill, computed
    only when some formation is outside the ranged net. Same ZOC rule as `trace`."""
    if info["connected"] is None:
        world_ok = move_costs(state, LAND)
        zoc, blocked = info["zoc"], info["blocked"]
        seen = set(info["starts"])
        stack = list(seen)
        while stack:
            x, y = stack.pop()
            if (x, y) in zoc and (x, y) not in info["starts"]:
                continue
            for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if nxt in world_ok and nxt not in blocked and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        info["connected"] = seen
    return info["connected"]


def _template(state: GameState, unit: Unit) -> dict:
    return state.template(unit.unit_type)


def _swaps(state: GameState, unit: Unit) -> list[tuple[str, str, float]]:
    """Active modernizations for this formation: (old item, new item, quantity ratio)."""
    known = state.nations[unit.nation_id].known_techs
    swaps = []
    for tech_id, table in _template(state, unit).get("modernize", {}).items():
        if tech_id in known:
            for old, new in table.items():
                new_id, ratio = (new[0], float(new[1])) if isinstance(new, list) else (new, 1.0)
                swaps.append((old, new_id, ratio))
    return swaps


def establishment(state: GameState, unit: Unit) -> dict[str, int]:
    """What the formation should carry: its template loadout, plus the `upgrades` of every tech its
    nation has researched, with `modernize` swaps applied, scaled by current strength."""
    template = _template(state, unit)
    share = unit.strength / max(1, template["manpower"])
    loadout = dict(template.get("loadout", {}))
    known = state.nations[unit.nation_id].known_techs
    for tech_id, extra in template.get("upgrades", {}).items():
        if tech_id in known:
            for item, qty in extra.items():
                loadout[item] = loadout.get(item, 0) + qty
    swaps = _swaps(state, unit)
    if swaps:
        items = {e["id"]: e for e in state.catalog["equipment"]}
        inv = unit.equipment_inventory
        converted = 1.0  # share of the weapons already re-armed (drives the ammunition mix)
        for old, new, _ in swaps:
            if items.get(old, {}).get("category") in WEAPON_CATEGORIES:
                total = inv.get(old, 0) + inv.get(new, 0)
                converted = min(converted, inv.get(new, 0) / total if total else 1.0)
        for old, new, ratio in swaps:
            qty = loadout.pop(old, 0)
            if items.get(old, {}).get("category") in WEAPON_CATEGORIES:
                loadout[new] = loadout.get(new, 0) + qty
            else:  # ammunition follows the weapons that fire it
                loadout[new] = loadout.get(new, 0) + qty * ratio * converted
                if converted < 1.0:
                    loadout[old] = qty * (1.0 - converted)
    return {item: int(round(qty * share)) for item, qty in loadout.items()}


def fill_ratio(state: GameState, unit: Unit, categories: tuple[str, ...] = ("ammunition",)) -> float:
    """Inventory / establishment for items in the given categories (1.0 if it needs none)."""
    items = {e["id"]: e for e in state.catalog["equipment"]}
    have = want = 0
    for item_id, wanted in establishment(state, unit).items():
        if items.get(item_id, {}).get("category") in categories:
            want += wanted
            have += min(wanted, unit.equipment_inventory.get(item_id, 0))
    return have / want if want else 1.0


def burn_fuel(state: GameState, unit: Unit) -> None:
    use = _template(state, unit).get("fuel_use", {})
    drums = float(use.get(unit.status, use.get("holding", 0)))
    if unit.status == MOVING and state.role(unit) == "armor":
        from src.engine.weather_engine import condition  # avoid import cycle

        drums *= float(condition(state).get("armor_fuel", 1.0))  # the Rasputitsa: tracks churn in the mud
    drums = int(round(drums))
    if drums:
        unit.equipment_inventory["fuel_drums"] = max(0, unit.equipment_inventory.get("fuel_drums", 0) - drums)


def turn_in_superseded(state: GameState, nation: Nation, unit: Unit) -> None:
    """Old kit beyond what the establishment still wants goes back to the depots (e.g. 7.62mm rifles
    once 5.56mm rifles have arrived)."""
    swaps = _swaps(state, unit)
    if not swaps:
        return
    wanted = establishment(state, unit)
    for old, _, _ in swaps:
        surplus = unit.equipment_inventory.get(old, 0) - wanted.get(old, 0)
        if surplus <= 0:
            continue
        items = {e["id"]: e for e in state.catalog["equipment"]}
        if items.get(old, {}).get("category") in WEAPON_CATEGORIES:
            # Rifles are handed in one-for-one as the new ones are issued: never leave men unarmed.
            new = next(n for o, n, _ in swaps if o == old)
            armed = unit.equipment_inventory.get(old, 0) + unit.equipment_inventory.get(new, 0)
            surplus = min(surplus, max(0, armed - wanted.get(new, 0)))
        if surplus > 0:
            unit.equipment_inventory[old] -= surplus
            nation.national_stockpile[old] = nation.national_stockpile.get(old, 0) + surplus


def resupply_nation(state: GameState, nation: Nation, info: dict) -> None:
    """Move equipment from the national stockpile to supplied formations, neediest-and-fighting first."""
    cfg = _cfg(state).get("resupply", {})
    items = {e["id"]: e for e in state.catalog["equipment"]}
    hi, lo = float(cfg.get("consumable_max", 0.6)), float(cfg.get("consumable_min", 0.25))
    heavy = float(cfg.get("heavy_rate", 0.12))
    queue = [u for u in nation.units if u.supply_state == SUPPLIED and u.status != ROUTING]
    queue.sort(key=lambda u: (not u.engaged, fill_ratio(state, u)))
    for unit in queue:
        _, reach = supply_status(state, unit, info)
        for item_id, wanted in establishment(state, unit).items():
            gap = wanted - unit.equipment_inventory.get(item_id, 0)
            if gap <= 0:
                continue
            consumable = items.get(item_id, {}).get("category") in ("ammunition", "consumable")
            cap = wanted * ((hi - (hi - lo) * reach) if consumable else heavy)
            take = int(min(gap, max(1, cap), nation.national_stockpile.get(item_id, 0)))
            if take > 0:
                nation.national_stockpile[item_id] -= take
                unit.equipment_inventory[item_id] = unit.equipment_inventory.get(item_id, 0) + take
        turn_in_superseded(state, nation, unit)
        # Replacements and rest for formations out of the line.
        if not unit.engaged:
            template = _template(state, unit)
            gap = template["manpower"] - unit.strength
            men = int(min(gap, template["manpower"] * float(cfg.get("replacement_rate", 0.04)), nation.manpower))
            if men > 0:
                unit.strength += men
                nation.adjust_manpower(-men)
            unit.morale = min(float(cfg.get("morale_ceiling", 85)), unit.morale + float(cfg.get("morale_recovery", 2)))


def update_supply(state: GameState) -> list[Unit]:
    """Weekly consumption, delivery and attrition. Returns friendly units newly ISOLATED."""
    from src.engine.weather_engine import condition  # avoid import cycle

    cfg = _cfg(state)
    use = cfg.get("consumption", {})
    weather_supply = float(condition(state).get("supply", 1.0))
    newly_isolated = []
    for nation_id, nation in state.nations.items():
        info = compute_network(state, nation_id)
        for unit in nation.units:
            burn_fuel(state, unit)
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
                delivered = (hi - (hi - lo) * reach) * (weather_supply if state.domain(unit) == LAND else 1.0)
            unit.supply = max(0.0, min(100.0, unit.supply - consumed + delivered))
            if unit.supply <= 0:
                floor = int(_template(state, unit)["manpower"] * float(cfg.get("min_strength_fraction", 0.1)))
                loss = max(1, round(unit.strength * float(cfg.get("attrition_rate", 0.04))))
                unit.strength = max(floor, unit.strength - loss)
                unit.morale = max(0.0, unit.morale - float(cfg.get("attrition_morale", 5)))
        resupply_nation(state, nation, info)
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
    visible = [u for u in state.visible_hostiles() if state.domain(u) == LAND]
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
    if not info:
        return False
    if state.domain(unit) == SEA:
        return unit.active_order.target in info.get("sea", {})
    return unit.active_order.target in info["network"]


def speed_factor(state: GameState, unit: Unit) -> float:
    from src.engine.weather_engine import movement_factor  # avoid import cycle

    cfg = _cfg(state)
    factor = movement_factor(state, unit)
    if unit.supply < float(cfg.get("low_supply_threshold", 25)):
        factor *= float(cfg.get("low_supply_speed_factor", 0.5))
    needs_fuel = _template(state, unit).get("fuel_use", {}).get("moving", 0) > 0
    if needs_fuel and unit.equipment_inventory.get("fuel_drums", 0) <= 0:
        factor *= 0.25  # out of fuel: trucks, tanks and ships barely move; men walk
    return factor


def isolation_email(state: GameState, unit: Unit) -> Email:
    tpl = state.catalog["generated"]["isolated"]
    variables = state.text_vars() | {
        "unit": unit.name,
        "designation": unit.designation,
        "x": f"{unit.x:03d}",
        "y": f"{unit.y:03d}",
        "sector": state.world_map.place_name(*unit.location),
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
