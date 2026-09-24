"""Naval warfare: fleets on the map, missions, blockades, naval battles, shore bombardment, storms.

Warships (units.json domain "sea": Destroyer Flotillas [D], Battleship Squadrons [B], Submarine
Wolfpacks [U]) sail only on open water, are supplied from friendly unblockaded harbours
(logistics_engine) and muster at a harbour when raised. Each carries a MISSION (War Room: T cycles it):

  PATROL    hold station; fight any enemy fleet that comes into contact.
  BLOCKADE  an enemy port within naval.blockade_range rows is CLOSED while no warship of the port's owner
            lies within the same range to contest it. A closed port stops supplying the owner's armies
            and its overseas trade (world.json port `trade`) is lost from the weekly ledger.
  BOMBARD   fire support: the ship's guns (soft_attack x naval.bombard_multiplier) join the nearest
            friendly land battle within naval.bombard_range rows. Consumes naval_shells.

NAVAL BATTLE — when hostile fleets make contact (movement), every engaged fleet fires each week:
each warship fires only while it has ammunition (naval_shells for guns, torpedoes for submarines).
A side's hard_attack x naval.damage_per_attack becomes damage to the enemy's HULL POINTS (warship
`hull` x count), split between surface ships and submarines by hull share. Only ASW weapons (destroyer
guns and depth charges) hit submarines properly; other fire does naval.non_asw_vs_submarine of its
damage to them. Each unit loses the same FRACTION of crew as of hull (divided by its template
hull_defense); ships are lost whole, with the fractional part rolled (a ship is sunk or it is not).
A fleet whose morale falls below naval.retire_morale RETIRES toward its harbour; one with no ships
left is SUNK. SITREPs and After Action Reports follow, as for land battles.

STORMS (weather_engine): warships not in port lose weather.storm.attrition of their strength.
"""

from __future__ import annotations

import math

from src.engine.event_manager import deliver
from src.engine.movement import SEA, OrderError, issue_move_order, passable, refresh_engagements, scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import BLOCKADE, BOMBARD, MISSIONS, PATROL, ROUTING, Battle, Email, GameState, Unit

MISSION_CYCLE = (PATROL, BLOCKADE, BOMBARD)


class MissionError(ValueError):
    """An invalid naval mission order."""


def _cfg(state: GameState) -> dict:
    return state.config.get("naval", {})


def _items(state: GameState) -> dict[str, dict]:
    return {e["id"]: e for e in state.catalog["equipment"]}


def is_naval(state: GameState, unit: Unit) -> bool:
    return state.domain(unit) == SEA


def fleets(state: GameState, nation_id: str | None = None) -> list[Unit]:
    return [u for u in state.all_units() if is_naval(state, u) and (nation_id is None or u.nation_id == nation_id)]


def set_mission(state: GameState, unit_id: str, mission: str, *, nation_id: str | None = None) -> None:
    unit = state.unit(unit_id)
    if unit is None or unit.nation_id != (nation_id or state.player.id):
        raise MissionError(f"No such formation under your command: {unit_id}")
    if not is_naval(state, unit):
        raise MissionError(f"{unit.designation} is not a naval formation.")
    if mission not in MISSIONS:
        raise MissionError(f"Unknown mission {mission!r}")
    if unit.routing:
        raise MissionError(f"{unit.designation} has retired from action and will not answer orders.")
    unit.mission = mission


def next_mission(mission: str) -> str:
    return MISSION_CYCLE[(MISSION_CYCLE.index(mission) + 1) % len(MISSION_CYCLE)] if mission in MISSION_CYCLE else PATROL


# --- ports, harbours, blockades -------------------------------------------------------------


def own_harbours(state: GameState, nation_id: str, *, open_only: bool = True) -> list[tuple[int, int]]:
    world = state.world_map
    return [p.harbour for p in world.ports()
            if p.harbour and world.owner_at(p.x, p.y) == nation_id and (not open_only or p.name not in state.blockades)]


def home_harbour(state: GameState, unit: Unit) -> tuple[int, int] | None:
    options = own_harbours(state, unit.nation_id) or own_harbours(state, unit.nation_id, open_only=False)
    if not options:
        return None
    return min(options, key=lambda c: scaled_distance(state, c, unit.location))


def in_port(state: GameState, unit: Unit) -> bool:
    reach = float(_cfg(state).get("in_port_range", 1.5))
    return any(scaled_distance(state, h, unit.location) <= reach for h in own_harbours(state, unit.nation_id, open_only=False))


def blockade_status(state: GameState) -> dict[str, str]:
    """port name -> nation id blockading it, computed from the fleets' current positions and missions."""
    world = state.world_map
    reach = float(_cfg(state).get("blockade_range", 3.0))
    closed: dict[str, str] = {}
    ships = [u for u in fleets(state) if not u.routing]
    for port in world.ports():
        owner = world.owner_at(port.x, port.y)
        if owner not in state.nations:
            continue  # neutral harbours are not blockaded
        near = [u for u in ships if scaled_distance(state, u.location, port.location) <= reach]
        defenders = [u for u in near if u.nation_id == owner]
        raiders = [u for u in near if u.nation_id != owner and u.mission == BLOCKADE]
        if raiders and not defenders:
            closed[port.name] = max(raiders, key=lambda u: u.strength).nation_id
    return closed


def blockading(state: GameState, unit: Unit) -> list[str]:
    """Ports this ship is currently helping to close."""
    world = state.world_map
    reach = float(_cfg(state).get("blockade_range", 3.0))
    return [name for name, holder in state.blockades.items()
            if holder == unit.nation_id and unit.mission == BLOCKADE
            and (port := world.feature_named(name)) and scaled_distance(state, unit.location, port.location) <= reach]


def lost_trade(state: GameState, nation_id: str) -> int:
    world = state.world_map
    return sum(p.trade for p in world.ports() if p.name in state.blockades and world.owner_at(p.x, p.y) == nation_id)


def port_trade(state: GameState, nation_id: str) -> tuple[int, int]:
    """(overseas trade flowing this week, ports open) for a nation."""
    world = state.world_map
    ports = [p for p in world.ports() if world.owner_at(p.x, p.y) == nation_id]
    open_ports = [p for p in ports if p.name not in state.blockades]
    return sum(p.trade for p in open_ports), len(open_ports)


# --- shore bombardment -------------------------------------------------------------------------


def bombardment(state: GameState, nation_id: str, engaged: list[Unit], used: set[str]) -> tuple[float, float, list[Unit]]:
    """Naval gunfire for a land battle: (soft, hard, ships firing) for `nation_id`. Consumes shells."""
    from src.engine.combat_engine import firepower  # avoid import cycle

    reach = float(_cfg(state).get("bombard_range", 2.5))
    mult = float(_cfg(state).get("bombard_multiplier", 1.0))
    soft = hard = 0.0
    firing = []
    for ship in fleets(state, nation_id):
        if ship.id in used or ship.mission != BOMBARD or ship.engaged or ship.routing:
            continue
        if not any(scaled_distance(state, ship.location, u.location) <= reach for u in engaged):
            continue
        fire = firepower(state, ship)
        if fire.soft + fire.hard <= 1:
            continue
        used.add(ship.id)
        firing.append(ship)
        soft += fire.soft * mult
        hard += fire.hard * mult * 0.5
    return soft, hard, firing


# --- naval battle -------------------------------------------------------------------------------


def _hull(state: GameState, unit: Unit) -> float:
    items = _items(state)
    return sum(count * float(items.get(i, {}).get("combat", {}).get("hull", 0)) for i, count in unit.equipment_inventory.items()
               if count > 0)


def ships_left(state: GameState, unit: Unit) -> int:
    items = _items(state)
    return sum(c for i, c in unit.equipment_inventory.items() if items.get(i, {}).get("category") == "warship" and c > 0)


def naval_fire(state: GameState, unit: Unit) -> tuple[float, float, dict[str, int]]:
    """(total hard attack, ASW share of it, ammunition expended) for one week of naval action."""
    items = _items(state)
    inv = unit.equipment_inventory
    total = asw = 0.0
    spent: dict[str, int] = {}
    for item_id in sorted(inv):
        combat = items.get(item_id, {}).get("combat", {})
        count = inv.get(item_id, 0)
        hard = float(combat.get("hard_attack", 0))
        if count <= 0 or hard <= 0 or items[item_id].get("category") != "warship":
            continue
        firing = float(count)
        ammo_id, per = combat.get("uses_ammo"), float(combat.get("ammo_per_week", 0))
        if ammo_id and per > 0:
            firing = min(firing, inv.get(ammo_id, 0) / per)
            used = min(inv.get(ammo_id, 0), math.ceil(firing * per))
            inv[ammo_id] = inv.get(ammo_id, 0) - used
            spent[ammo_id] = spent.get(ammo_id, 0) + used
        total += firing * hard
        if combat.get("asw"):
            asw += firing * hard
    return total, asw, spent


def _damage_unit(state: GameState, unit: Unit, frac: float, battle: Battle | None = None) -> int:
    """Lose `frac` of crew and hulls (ships whole, fractional part rolled). Returns men lost."""
    frac = max(0.0, min(1.0, frac))
    items = _items(state)
    lost_men = min(unit.strength, int(round(unit.strength * frac)))
    unit.strength -= lost_men
    for item_id, count in list(unit.equipment_inventory.items()):
        item = items.get(item_id, {})
        if count <= 0:
            continue
        if item.get("category") == "warship":
            expected = count * frac
            sunk = int(expected) + (1 if state.rng.random() < expected - int(expected) else 0)
        else:
            sunk = int(round(count * frac * float(item.get("combat", {}).get("loss_rate", 0.0))))
        sunk = min(count, sunk)
        if sunk > 0:
            unit.equipment_inventory[item_id] = count - sunk
            if battle is not None:
                battle.equipment_lost[unit.nation_id][item_id] += sunk
    return lost_men


def retire(state: GameState, unit: Unit, cells: int) -> None:
    """Break off toward the home harbour, one sea cell at a time, never onto an enemy ship."""
    target = home_harbour(state, unit) or unit.location
    enemies = [u for u in fleets(state) if u.nation_id != unit.nation_id]
    for _ in range(cells):
        x, y = unit.location
        options = []
        for cell in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not passable(state, *cell, SEA) or any(e.location == cell for e in enemies):
                continue
            nearest = min((scaled_distance(state, cell, e.location) for e in enemies), default=99.0)
            options.append((scaled_distance(state, cell, target) - 2.0 * min(nearest, 3.0), cell))
        if not options:
            return
        unit.location = min(options)[1]


def fight_naval_round(state: GameState, battle: Battle, group: set[str]):
    from src.engine.combat_engine import RoundResult  # avoid import cycle

    cfg = _cfg(state)
    combat_cfg = state.config.get("combat", {})
    rng = state.rng
    units = [state.unit(i) for i in sorted(group) if state.unit(i) is not None]
    nations = sorted({u.nation_id for u in units})
    side = {n: [u for u in units if u.nation_id == n] for n in nations}
    lo, hi = combat_cfg.get("noise", [0.8, 1.2])

    # 1. Broadsides and torpedo spreads, simultaneously.
    output: dict[str, tuple[float, float]] = {}
    starved: list[Unit] = []
    for n in nations:
        total = asw = 0.0
        for unit in side[n]:
            hard, anti_sub, spent = naval_fire(state, unit)
            for item_id, used in spent.items():
                battle.ammo_expended[n][item_id] += used
            if hard <= 0:
                starved.append(unit)
            mult = (0.5 + unit.morale / 200) * rng.uniform(lo, hi)
            if unit.supply < float(state.config.get("logistics", {}).get("low_supply_threshold", 25)):
                mult *= float(combat_cfg.get("low_supply_factor", 0.6))
            total += hard * mult
            asw += anti_sub * mult
        output[n] = (total, asw)

    # 2. Damage by hull share: submarines are hit properly only by ASW fire.
    k = float(cfg.get("damage_per_attack", 0.35))
    vs_sub = float(cfg.get("non_asw_vs_submarine", 0.1))
    casualties = {n: 0 for n in nations}
    for n in nations:
        total = sum(output[m][0] for m in nations if m != n)
        asw = sum(output[m][1] for m in nations if m != n)
        surface = [u for u in side[n] if not state.template(u.unit_type).get("stealth")]
        subs = [u for u in side[n] if state.template(u.unit_type).get("stealth")]
        surf_hull = sum(_hull(state, u) for u in surface)
        sub_hull = sum(_hull(state, u) for u in subs)
        pool = max(1e-9, surf_hull + sub_hull)
        non_asw = total - asw
        to_surface = (non_asw + asw * surf_hull / pool) if surf_hull else 0.0
        to_subs = (asw * sub_hull / pool + non_asw * vs_sub) if sub_hull else 0.0
        for group_units, damage, hull in ((surface, to_surface, surf_hull), (subs, to_subs, sub_hull)):
            if not group_units or hull <= 0:
                continue
            for unit in group_units:
                defense = float(state.template(unit.unit_type).get("hull_defense", 1.0))
                frac = damage * k / hull / defense
                lost = _damage_unit(state, unit, frac, battle)
                casualties[n] += lost
                morale_hit = frac * 100 * 1.5 + float(combat_cfg.get("combat_stress", 1.5))
                if unit in starved:
                    morale_hit += float(combat_cfg.get("ammo_starved_morale", 5))
                unit.morale = max(0.0, unit.morale - morale_hit)
        battle.casualties[n] += casualties[n]

    # 3. Sunk or retiring.
    routed, destroyed = [], []
    for unit in units:
        if unit.strength <= 0 or ships_left(state, unit) <= 0:
            destroyed.append(unit)
            battle.destroyed.append(unit.id)
            state.remove_unit(unit)
            continue
        establishment = state.template(unit.unit_type)["manpower"]
        if unit.morale < float(cfg.get("retire_morale", 20)) or unit.strength < establishment * 0.1:
            unit.status = ROUTING
            unit.routing_weeks = int(combat_cfg.get("rout_weeks", 2))
            unit.active_order = None
            unit.move_points = 0.0
            for other_id in list(unit.engaged_with):
                state.engagements.discard(frozenset((unit.id, other_id)))
                other = state.unit(other_id)
                if other is not None and unit.id in other.engaged_with:
                    other.engaged_with.remove(unit.id)
            unit.engaged_with = []
            retire(state, unit, int(cfg.get("retire_cells", 4)))
            routed.append(unit)
            battle.routed.append(unit.id)
    refresh_engagements(state)
    battle.weeks += 1
    return RoundResult(battle, casualties, starved, routed, destroyed, [])


# --- storms -------------------------------------------------------------------------------------


def storm_damage(state: GameState) -> dict[str, int]:
    """Ships at sea in a storm founder: returns men lost per nation."""
    from src.engine.weather_engine import storm_at_sea

    if not storm_at_sea(state):
        return {}
    storm = state.catalog.get("weather", {}).get("storm", {})
    losses: dict[str, int] = {}
    for ship in fleets(state):
        if in_port(state, ship):
            continue
        lost = _damage_unit(state, ship, float(storm.get("attrition", 0.03)))
        ship.morale = max(0.0, ship.morale - float(storm.get("morale", 3)))
        losses[ship.nation_id] = losses.get(ship.nation_id, 0) + lost
        if ships_left(state, ship) <= 0 or ship.strength <= 0:
            state.remove_unit(ship)
    return losses


# --- dispatches ---------------------------------------------------------------------------------


def blockade_email(state: GameState, port: str, holder: str, imposed: bool) -> Email:
    world = state.world_map
    feature = world.feature_named(port)
    ours = world.owner_at(feature.x, feature.y) == state.player.id
    tpl = state.catalog["generated"]["blockade"]
    if ours and imposed:
        subject = f"NAVAL BLOCKADE: {port.upper()} CLOSED"
        body = [f"Enemy warships are lying off {port} (grid {feature.x:03d}-{feature.y:03d}) and no ship of ours contests "
                "them. The harbour is closed.",
                "",
                f"  Overseas trade lost ........ {feature.trade:,} {state.currency} per week",
                "  The port no longer supplies our armies.",
                "",
                "To break the blockade, send warships to within reach of the port: a contested blockade fails. "
                "Enemy submarines are hunted best by destroyers."]
    elif ours:
        subject = f"BLOCKADE LIFTED: {port.upper()} REOPENED"
        body = [f"The enemy squadron off {port} has gone. Merchant traffic is moving again and the port once more supplies "
                f"our armies (+{feature.trade:,} {state.currency} per week in trade)."]
    elif imposed:
        subject = f"BLOCKADE ESTABLISHED: {port.upper()}"
        body = [f"Our warships have closed the Vosk port of {port}. Its overseas trade and the supply it gave the "
                "enemy's armies are cut for as long as the blockade holds.",
                "",
                "Hold station on the BLOCKADE mission. If an enemy squadron comes out to contest it, the port reopens."]
    else:
        subject = f"BLOCKADE BROKEN: {port.upper()}"
        body = [f"Our blockade of {port} has lapsed: our ships have left the station or the enemy is contesting it."]
    body += ["", "— Admiralty Operations Room"]
    variables = state.text_vars() | {"port": port}
    return Email(id="naval_blockade", sender=fill(tpl["sender"], variables), subject=subject,
                 classification=tpl.get("classification", "SECRET"), body="\n".join(body))


# --- the Vosk admiralty (called by the AI Director) -----------------------------------------------


def ai_naval_orders(state: GameState, ai) -> list[tuple[Unit, tuple[int, int], str]]:
    """Orders for the AI's fleets: (ship, target sea cell, mission)."""
    cfg = ai.config.get("naval", {})
    world = state.world_map
    orders: list[tuple[Unit, tuple[int, int], str]] = []
    enemies = [u for u in fleets(state) if u.nation_id != ai.nation_id and not u.routing]
    kestrian_ports = sorted((p for p in world.ports() if world.owner_at(p.x, p.y) == state.player.id and p.harbour),
                            key=lambda p: (-p.trade, p.name))
    claimed: set[str] = set()
    for ship in sorted(fleets(state, ai.nation_id), key=lambda u: u.id):
        if ship.engaged or ship.routing:
            continue
        template = state.template(ship.unit_type)
        home = home_harbour(state, ship)
        if ship.supply < float(cfg.get("resupply_threshold", 35)) and ship.supply_state != "supplied" and home:
            orders.append((ship, home, PATROL))
            continue
        near = [e for e in enemies if scaled_distance(state, e.location, ship.location) <= float(cfg.get("hunt_range", 20))]
        if near and not template.get("stealth"):
            prey = min(near, key=lambda e: scaled_distance(state, e.location, ship.location))
            orders.append((ship, prey.location, PATROL))
            continue
        if ai.posture == "DEFEND":
            if home and scaled_distance(state, home, ship.location) > 6:
                orders.append((ship, home, PATROL))
            continue
        if "battleship" in ship.equipment_inventory:
            target = _bombard_station(state, ai.nation_id, ship)
            if target:
                orders.append((ship, target, BOMBARD))
                continue
        for port in kestrian_ports:
            if port.name in claimed:
                continue
            station = _station_near(state, port.location, float(cfg.get("blockade_standoff", 2)), ship.location)
            if station:
                claimed.add(port.name)
                orders.append((ship, station, BLOCKADE))
                break
    return orders


def _station_near(state: GameState, point: tuple[int, int], reach: float, origin: tuple[int, int]) -> tuple[int, int] | None:
    x0, y0 = point
    span = int(reach / 0.5) + 1
    cells = [(x, y) for x in range(x0 - span, x0 + span + 1) for y in range(y0 - int(reach) - 1, y0 + int(reach) + 2)
             if passable(state, x, y, SEA) and scaled_distance(state, (x, y), point) <= reach]
    return min(cells, key=lambda c: (scaled_distance(state, c, origin), c)) if cells else None


def _bombard_station(state: GameState, nation_id: str, ship: Unit) -> tuple[int, int] | None:
    reach = float(_cfg(state).get("bombard_range", 2.5)) - 0.5
    engaged = [u for u in state.nations[nation_id].units if u.engaged and state.domain(u) != SEA]
    stations = [s for u in engaged if (s := _station_near(state, u.location, reach, ship.location))]
    return min(stations, key=lambda c: scaled_distance(state, c, ship.location)) if stations else None


def issue_ai_naval_orders(state: GameState, ai, report: TickReport) -> None:
    for ship, target, mission in ai_naval_orders(state, ai):
        ship.mission = mission
        if ship.location == target:
            continue
        try:
            issue_move_order(state, ship.id, target, nation_id=ai.nation_id)
        except OrderError:
            continue
        ai.log.append(f"WK {state.clock.turn:03d}: {ship.designation} -> {target} ({mission.upper()})")


# --- tick system ----------------------------------------------------------------------------------


class NavalSystem(SimulationSystem):
    """After combat: blockades are imposed or lifted; storms batter ships at sea."""

    name = "naval"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        losses = storm_damage(state)
        if losses.get(state.player.id):
            report.log.append(f"STORMS AT SEA: {losses[state.player.id]:,} sailors lost.")
        before = dict(state.blockades)
        state.blockades = blockade_status(state)
        world = state.world_map
        for port, holder in state.blockades.items():
            if port not in before:
                report.new_messages.append(deliver(state, blockade_email(state, port, holder, imposed=True)))
                report.log.append(f"BLOCKADE: {port} closed.")
        for port, holder in before.items():
            if port not in state.blockades:
                report.new_messages.append(deliver(state, blockade_email(state, port, holder, imposed=False)))
                report.log.append(f"Blockade of {port} lifted.")
        ours = [p for p in state.blockades if world.owner_at(*world.feature_named(p).location) == state.player.id]
        if ours:
            report.log.append(f"Ports under blockade: {', '.join(ours)}.")
