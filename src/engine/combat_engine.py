"""Combat: the meatgrinder.

Every week, every group of formations locked in contact (a connected component of
`state.engagements`) fights one round of a multi-week Battle.

Firepower is PHYSICAL. For each weapon a formation carries (equipment.json `combat`):
  * it fires only while it has ammunition: `ammo_per_week` x stance.ammo rounds per weapon,
    drawn from the unit's own inventory;
  * tanks also burn `fuel_per_week` fuel drums; no fuel, no tank;
  * each firing weapon adds soft_attack (vs men) and hard_attack (vs armor).
A formation that has run dry fights with bayonets: its output drops to near zero.

    damage  = Σ(firepower) x stance.attack x (0.5 + morale/200) x supply factor x noise
    applied = soft x (1 - H) + hard x H      (H = armored share of the enemy side's strength)
    casualties to enemy unit i = applied x casualty_factor x (strength_i / Σ strength) / defense_i
    defense_i = terrain defense x fortification x stance.defense x body armor

Fortification: within `fort_range` columns of the unit's OWN trench line; urban fortification in urban
terrain or within settlement_fortification_range rows of a town or city; never for formations in ASSAULT
stance (they have gone over the top).

COMBINED ARMS (Phase 7) — each side's weekly damage is increased by:
  * ARTILLERY support: artillery formations (template `support`) NOT themselves in contact, within
    artillery_support_range rows of one of the side's engaged formations, fire for that battle: their
    guns' firepower x artillery_support_multiplier (thousands of shells a week). They take no casualties
    unless the enemy reaches them. Each battery supports one battle a week.
  * NAVAL GUNFIRE: warships on a BOMBARD mission within naval.bombard_range rows (naval_engine).
  * AIR SUPPORT: x(1 + air bonus) when the side holds air superiority over the battle's sector (air_engine).
  * WEATHER: formations without winter kit fight at the freezing condition's `combat` factor.
Naval battles (fleets in contact at sea) are resolved by naval_engine.fight_naval_round.
Casualties destroy equipment too (equipment `loss_rate` x casualty fraction) and cost morale.

A formation BREAKS when morale < rout_morale or strength < rout_strength_fraction of its
establishment: it ROUTS (bonus casualties, falls back toward its HQ, cannot take orders for
`rout_weeks`) and the victorious enemy takes its cell. At 0 men it is DESTROYED. Formations in
WITHDRAW stance fall back in good order every week and break contact.

The battle ends when nobody in it is still in contact. The side still holding the field wins.
Each week the player receives a SITREP per battle; at the end, an AFTER ACTION REPORT. Enemy
figures in both are estimates (fog of war).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from src.engine.event_manager import deliver
from src.engine.intel import estimate
from src.engine.movement import SEA, passable, refresh_engagements, scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import ASSAULT, DEFEND, ENGAGED, HOLDING, ROUTING, STANCES, WITHDRAW, Battle, Email, GameState, Unit

ORDINALS = ["", "Second ", "Third ", "Fourth ", "Fifth ", "Sixth ", "Seventh ", "Eighth ", "Ninth ", "Tenth "]


class StanceError(ValueError):
    """An invalid stance change."""


def _cfg(state: GameState) -> dict:
    return state.config.get("combat", {})


def _template(state: GameState, unit: Unit) -> dict:
    return state.template(unit.unit_type)


def _stance(state: GameState, stance_id: str) -> dict:
    return next((s for s in state.catalog["units"]["stances"] if s["id"] == stance_id),
                {"attack": 1.0, "defense": 1.0, "ammo": 1.0})


def _items(state: GameState) -> dict[str, dict]:
    return {e["id"]: e for e in state.catalog["equipment"]}


def set_stance(state: GameState, unit_id: str, stance: str, *, nation_id: str | None = None) -> None:
    unit = state.unit(unit_id)
    if unit is None or unit.nation_id != (nation_id or state.player.id):
        raise StanceError(f"No such formation under your command: {unit_id}")
    if stance not in STANCES:
        raise StanceError(f"Unknown stance {stance!r}")
    if unit.status == ROUTING:
        raise StanceError(f"{unit.designation} is routing and will not answer orders.")
    unit.stance = stance


# --- firepower ------------------------------------------------------------------------


@dataclass
class Fire:
    soft: float = 0.0
    hard: float = 0.0
    starved: bool = False  # less than half its weapons could fire
    expended: dict[str, int] = field(default_factory=dict)


def firepower(state: GameState, unit: Unit, consume: bool = True) -> Fire:
    """Raw weekly firepower from weapons that can actually fire; consumes their ammo and fuel."""
    items = _items(state)
    inv = unit.equipment_inventory
    ammo_mult = float(_stance(state, unit.stance).get("ammo", 1.0))
    fire = Fire()
    potential = actual = 0.0
    for item_id in sorted(inv):
        count = inv.get(item_id, 0)
        combat = items.get(item_id, {}).get("combat", {})
        soft, hard = float(combat.get("soft_attack", 0)), float(combat.get("hard_attack", 0))
        if count <= 0 or (soft == 0 and hard == 0):
            continue
        firing = float(count)
        ammo_id, per_ammo = combat.get("uses_ammo"), float(combat.get("ammo_per_week", 0)) * ammo_mult
        if ammo_id and per_ammo > 0:
            firing = min(firing, inv.get(ammo_id, 0) / per_ammo)
        per_fuel = float(combat.get("fuel_per_week", 0))
        if per_fuel > 0:
            firing = min(firing, inv.get("fuel_drums", 0) / per_fuel)
        potential += count * (soft + hard)
        actual += firing * (soft + hard)
        fire.soft += firing * soft
        fire.hard += firing * hard
        if consume and firing > 0:
            if ammo_id and per_ammo > 0:
                used = min(inv.get(ammo_id, 0), math.ceil(firing * per_ammo))
                inv[ammo_id] = inv.get(ammo_id, 0) - used
                fire.expended[ammo_id] = fire.expended.get(ammo_id, 0) + used
            if per_fuel > 0:
                used = min(inv.get("fuel_drums", 0), math.ceil(firing * per_fuel))
                inv["fuel_drums"] = inv.get("fuel_drums", 0) - used
                fire.expended["fuel_drums"] = fire.expended.get("fuel_drums", 0) + used
    fire.starved = potential > 0 and actual / potential < 0.5
    # Force multipliers carried in quantity: APFSDS rounds (hard), night vision (all fire).
    from src.engine.logistics_engine import establishment  # avoid import cycle

    wanted = establishment(state, unit)
    for item_id, need in wanted.items():
        combat = items.get(item_id, {}).get("combat", {})
        if need and inv.get(item_id, 0) >= need * 0.5:
            fire.hard *= 1.0 + float(combat.get("hard_attack_bonus", 0.0))
            fire.soft *= 1.0 + float(combat.get("attack_bonus", 0.0))
            fire.hard *= 1.0 + float(combat.get("attack_bonus", 0.0))
    fire.soft += unit.strength * 0.0005  # bayonets, grenades, desperation: near zero
    return fire


# --- defense ----------------------------------------------------------------------------


def fortification(state: GameState, unit: Unit) -> float:
    """Trench lines (within fort_range columns) or urban terrain. Attackers have left their works."""
    if unit.stance == ASSAULT:
        return 1.0
    cfg = _cfg(state)
    world = state.world_map
    reach = int(cfg.get("fort_range", 1))
    for dx in range(-reach, reach + 1):
        x = unit.x + dx
        if world.transport_at(x, unit.y) == "trench" and trench_owner(state, x, unit.y) == unit.nation_id:
            return float(cfg.get("fortification", 1.6))
    region = world.region_at(*unit.location)
    if region is not None and region.terrain == "urban":
        return float(cfg.get("urban_fortification", 1.3))
    reach = float(cfg.get("settlement_fortification_range", 1))
    if any(scaled_distance(state, (f.x, f.y), unit.location) <= reach for f in world.features):
        return float(cfg.get("urban_fortification", 1.3))  # street fighting in a town or city
    return 1.0


def trench_owner(state: GameState, x: int, y: int) -> str | None:
    """Whose trench line this is: the nation whose own territory is nearest along the row.
    Captured enemy works face the wrong way and give the new occupant nothing."""
    key = ("trench_owner", x, y)
    if key not in state.cost_cache:
        world = state.world_map
        best, owner = None, None
        for direction in (-1, 1):
            cx = x
            while 0 <= cx < world.width:
                region_owner = world.owner_at(cx, y)
                if region_owner in state.nations:
                    distance = abs(cx - x)
                    if best is None or distance < best:
                        best, owner = distance, region_owner
                    break
                cx += direction
        state.cost_cache[key] = owner
    return state.cost_cache[key]


def defense(state: GameState, unit: Unit) -> float:
    world = state.world_map
    region = world.region_at(*unit.location)
    terrain = float(world.terrain_info(region.terrain).get("defense", 1.0)) if region else 1.0
    value = terrain * fortification(state, unit) * float(_stance(state, unit.stance).get("defense", 1.0))
    value *= 1.0 + state.nations[unit.nation_id].modifier(f"stance_defense:{unit.stance}")  # doctrine
    vests = unit.equipment_inventory.get("kevlar_vest", 0)
    if vests and vests >= unit.strength * 0.9:
        reduction = float(_items(state).get("kevlar_vest", {}).get("combat", {}).get("casualty_reduction", 1.0))
        value /= max(reduction, 0.1)
    return value


def _role(state: GameState, unit: Unit) -> str:
    return _template(state, unit).get("role", "infantry")


# --- battles ----------------------------------------------------------------------------


def battle_groups(state: GameState) -> list[set[str]]:
    """Connected components of the engagement graph."""
    adjacency: dict[str, set[str]] = {}
    for pair in state.engagements:
        a, b = tuple(pair)
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    seen: set[str] = set()
    groups = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack, group = [start], set()
        while stack:
            node = stack.pop()
            if node in group:
                continue
            group.add(node)
            stack.extend(adjacency[node] - group)
        seen |= group
        groups.append(group)
    return groups


def _battle_name(state: GameState, location: tuple[int, int]) -> str:
    place = state.world_map.place_name(*location)
    if place.startswith("The "):
        place = "the " + place[4:]
    base = f"Battle of {place}"
    earlier = sum(1 for b in state.battles.values() if b.name.endswith(base))
    return f"{ORDINALS[min(earlier, len(ORDINALS) - 1)]}{base}"


def _enlist(state: GameState, battle: Battle, group: set[str]) -> Battle:
    battle.participants |= group
    for unit_id in group:
        unit = state.unit(unit_id)
        if unit is not None:
            battle.roster[unit_id] = (unit.nation_id, f"{unit.designation} {unit.name}")
    return battle


def _battle_for(state: GameState, group: set[str]) -> Battle:
    for battle in state.battles.values():
        if battle.active and battle.participants & group:
            return _enlist(state, battle, group)
    units = [state.unit(i) for i in group]
    x = round(sum(u.x for u in units) / len(units))
    y = round(sum(u.y for u in units) / len(units))
    battle = Battle(
        id=f"battle_{len(state.battles) + 1:03d}",
        name=_battle_name(state, (x, y)),
        location=(x, y),
        started_turn=state.clock.turn,
    )
    state.battles[battle.id] = battle
    return _enlist(state, battle, group)


# --- movement out of battle ---------------------------------------------------------------


def _fallback_target(state: GameState, unit: Unit) -> tuple[int, int]:
    hqs = [u for u in state.nations[unit.nation_id].units if _role(state, u) == "hq" and u.id != unit.id]
    if hqs:
        return min(hqs, key=lambda h: scaled_distance(state, h.location, unit.location)).location
    from src.engine.logistics_engine import supply_sources

    sources = list(supply_sources(state, unit.nation_id)) or [unit.location]
    return min(sources, key=lambda c: scaled_distance(state, c, unit.location))


def fall_back(state: GameState, unit: Unit, cells: int) -> None:
    """Step up to `cells` cells toward the fallback target, away from the enemy, never onto an enemy."""
    target = _fallback_target(state, unit)
    enemies = [u for u in state.all_units() if u.nation_id != unit.nation_id and state.domain(u) != SEA]
    for _ in range(cells):
        options = []
        x, y = unit.location
        for cell in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not passable(state, *cell) or any(e.location == cell for e in enemies):
                continue
            nearest_enemy = min((scaled_distance(state, cell, e.location) for e in enemies), default=99.0)
            options.append((scaled_distance(state, cell, target) - 2.0 * min(nearest_enemy, 3.0), cell))
        if not options:
            return
        unit.location = min(options)[1]


# --- the round ---------------------------------------------------------------------------


def _choose_ai_stance(state: GameState, unit: Unit, own: float, enemy: float) -> str:
    from src.engine.logistics_engine import fill_ratio

    cfg = _cfg(state)
    ratio = own / max(enemy, 1)
    if unit.morale < float(cfg.get("ai_withdraw_morale", 30)) or ratio < float(cfg.get("ai_withdraw_ratio", 0.6)):
        return WITHDRAW
    if fill_ratio(state, unit) < 0.15:
        return WITHDRAW
    if ratio >= float(cfg.get("ai_assault_ratio", 1.5)):
        return ASSAULT
    return DEFEND


@dataclass
class RoundResult:
    battle: Battle
    casualties: dict[str, int]  # nation -> men lost this week
    starved: list[Unit]  # units that could not fire half their weapons
    routed: list[Unit]
    destroyed: list[Unit]
    withdrew: list[Unit]
    support: dict[str, list[str]] = field(default_factory=dict)  # nation -> fire-support lines (for the SITREP)


def artillery_support(state: GameState, nation_id: str, engaged: list[Unit], used: set[str]) -> tuple[float, float, list[str]]:
    """Guns behind the line: (soft, hard, report lines) for `nation_id` in one battle. Consumes their shells."""
    cfg = _cfg(state)
    reach = float(cfg.get("artillery_support_range", 2.5))
    mult = float(cfg.get("artillery_support_multiplier", 1.5))
    soft = hard = 0.0
    lines = []
    for gun in state.nations[nation_id].units:
        if gun.id in used or gun.engaged or gun.routing or not _template(state, gun).get("support"):
            continue
        if not any(scaled_distance(state, gun.location, u.location) <= reach for u in engaged):
            continue
        used.add(gun.id)
        fire = firepower(state, gun)
        shells = sum(v for k, v in fire.expended.items() if k != "fuel_drums")
        if shells <= 0:
            lines.append(f"{gun.designation} in range but OUT OF SHELLS")
            continue
        weather = _weather_combat(state, gun)
        soft += fire.soft * mult * weather
        hard += fire.hard * mult * weather
        lines.append(f"{gun.designation} {gun.name}: {shells:,} rounds fired in support")
    return soft, hard, lines


def _weather_combat(state: GameState, unit: Unit) -> float:
    from src.engine.weather_engine import combat_factor

    return combat_factor(state, unit)


def fight_round(state: GameState, battle: Battle, group: set[str]) -> RoundResult:
    cfg = _cfg(state)
    rng = state.rng
    units = [state.unit(i) for i in sorted(group) if state.unit(i) is not None]
    nations = sorted({u.nation_id for u in units})
    side = {n: [u for u in units if u.nation_id == n] for n in nations}
    strength = {n: sum(u.strength for u in side[n]) for n in nations}

    for n in nations:  # the AI picks stances by local superiority
        if n in state.ai_states:
            enemy = sum(strength[m] for m in nations if m != n)
            for unit in side[n]:
                unit.stance = _choose_ai_stance(state, unit, strength[n], enemy)

    # 1. Everyone fires simultaneously (ammunition is spent now), with artillery, naval and air support.
    from src.engine.air_engine import air_bonus
    from src.engine.naval_engine import bombardment

    output: dict[str, tuple[float, float]] = {}
    starved: list[Unit] = []
    support: dict[str, list[str]] = {}
    lo, hi = cfg.get("noise", [0.8, 1.2])
    supporters = state.cost_cache.setdefault(("support_used", state.clock.turn), set())
    for n in nations:
        soft = hard = 0.0
        lines: list[str] = []
        guns_soft, guns_hard, gun_lines = artillery_support(state, n, side[n], supporters)
        lines += gun_lines
        navy_soft, navy_hard, ships = bombardment(state, n, side[n], supporters)
        lines += [f"{s.designation} {s.name}: naval gunfire from offshore" for s in ships]
        soft += guns_soft + navy_soft
        hard += guns_hard + navy_hard
        for unit in side[n]:
            fire = firepower(state, unit)
            for item_id, used in fire.expended.items():
                battle.ammo_expended[n][item_id] += used
            if fire.starved:
                starved.append(unit)
            mult = float(_stance(state, unit.stance).get("attack", 1.0)) * (0.5 + unit.morale / 200)
            if unit.supply < float(state.config.get("logistics", {}).get("low_supply_threshold", 25)):
                mult *= float(cfg.get("low_supply_factor", 0.6))
            mult *= _weather_combat(state, unit)
            mult *= rng.uniform(lo, hi)
            soft += fire.soft * mult
            hard += fire.hard * mult
        air = air_bonus(state, n, battle.location)
        if air > 1.0:
            soft *= air
            hard *= air
            lines.append(f"Air superiority over the sector: +{air - 1:.0%} firepower")
        support[n] = lines
        output[n] = (soft, hard)

    # 2. Casualties to each side from everyone else.
    casualties = {n: 0 for n in nations}
    k = float(cfg.get("casualty_factor", 1.6))
    for n in nations:
        enemies = [m for m in nations if m != n]
        soft = sum(output[m][0] for m in enemies)
        hard = sum(output[m][1] for m in enemies)
        total = max(1, strength[n])
        armored = sum(u.strength for u in side[n] if _role(state, u) == "armor") / total
        applied = soft * (1 - armored) + hard * armored
        for unit in side[n]:
            before = unit.strength
            lost = min(before, int(round(applied * k * (before / total) / defense(state, unit))))
            unit.strength -= lost
            casualties[n] += lost
            frac = lost / max(1, before)
            for item_id, count in list(unit.equipment_inventory.items()):
                rate = float(_items(state).get(item_id, {}).get("combat", {}).get("loss_rate", 0.0))
                destroyed = int(round(count * frac * rate))
                if destroyed > 0:
                    unit.equipment_inventory[item_id] = count - destroyed
                    battle.equipment_lost[n][item_id] += destroyed
            morale_hit = frac * 100 * float(cfg.get("morale_per_casualty_pct", 2.0)) + float(cfg.get("combat_stress", 1.5))
            if unit in starved:
                morale_hit += float(cfg.get("ammo_starved_morale", 5))
            unit.morale = max(0.0, unit.morale - morale_hit)
        battle.casualties[n] += casualties[n]

    # 3. Breaks, routs, withdrawals.
    routed, destroyed, withdrew = [], [], []
    for unit in units:
        establishment = _template(state, unit)["manpower"]
        enemies_engaged = [state.unit(i) for i in unit.engaged_with if state.unit(i) is not None]
        if unit.strength <= 0:
            destroyed.append(unit)
            battle.destroyed.append(unit.id)
            state.remove_unit(unit)
            continue
        breaking = unit.morale < float(cfg.get("rout_morale", 15)) or \
            unit.strength < establishment * float(cfg.get("rout_strength_fraction", 0.1))
        if breaking:
            bonus = int(round(unit.strength * float(cfg.get("rout_bonus_casualties", 0.1))))
            unit.strength = max(1, unit.strength - bonus)
            battle.casualties[unit.nation_id] += bonus
            casualties[unit.nation_id] += bonus
            origin = unit.location
            unit.status = ROUTING
            unit.routing_weeks = int(cfg.get("rout_weeks", 2))
            unit.active_order = None
            unit.move_points = 0.0
            for other in enemies_engaged:
                state.engagements.discard(frozenset((unit.id, other.id)))
                if unit.id in other.engaged_with:
                    other.engaged_with.remove(unit.id)
            unit.engaged_with = []
            fall_back(state, unit, int(cfg.get("rout_retreat_cells", 2)))
            victors = [e for e in enemies_engaged if e.status != ROUTING and e.stance != WITHDRAW]
            if victors:  # the victor takes the ground
                victor = max(victors, key=lambda v: v.strength)
                if not any(u.location == origin and u.nation_id != victor.nation_id for u in state.all_units()):
                    victor.location = origin
            routed.append(unit)
            battle.routed.append(unit.id)
        elif unit.stance == WITHDRAW:
            fall_back(state, unit, int(cfg.get("withdraw_cells", 1)))
            withdrew.append(unit)
    for unit in withdrew:
        if unit.id not in battle.withdrew:
            battle.withdrew.append(unit.id)
    refresh_engagements(state)
    for unit in state.all_units():
        if unit.status == ROUTING:
            unit.engaged_with = []
    battle.weeks += 1
    return RoundResult(battle, casualties, starved, routed, destroyed, withdrew, support)


def _still_fighting(state: GameState, battle: Battle) -> bool:
    return any(pair <= battle.participants for pair in state.engagements)


def _decide(state: GameState, battle: Battle) -> str | None:
    """The side that still holds the field: alive, not routing, and not withdrawing."""
    holders = set()
    for unit_id in battle.participants:
        unit = state.unit(unit_id)
        if unit is None or unit.status == ROUTING or unit.stance == WITHDRAW:
            continue
        holders.add(unit.nation_id)
    return holders.pop() if len(holders) == 1 else None


# --- dispatches ---------------------------------------------------------------------------


def _est(state: GameState, battle: Battle, true_value: float, accuracy: float, salt: str) -> str:
    rng = random.Random(f"{state.intel_seed}:{battle.id}:{state.clock.turn}:{salt}")
    reading = estimate(rng, true_value, accuracy, 0.05)
    return f"{reading.low:,} – {reading.high:,} (est.)"


def _unit_line(state: GameState, unit: Unit) -> str:
    from src.engine.logistics_engine import fill_ratio

    ammo = fill_ratio(state, unit)
    stance = _stance(state, unit.stance).get("name", unit.stance).upper()
    flag = "  ! AMMUNITION CRITICAL" if ammo < 0.25 else ""
    return (f"  {unit.designation:<6} {unit.strength:>6,} men  morale {unit.morale:>3.0f}%  ammo {ammo:>4.0%}  "
            f"{stance:<8} {unit.status.upper()}{flag}")


def sitrep_email(state: GameState, result: RoundResult) -> Email:
    battle = result.battle
    player = state.player.id
    enemy_losses = sum(v for n, v in result.casualties.items() if n != player)
    ours = [state.unit(i) for i in sorted(battle.participants)]
    ours = [u for u in ours if u is not None and u.nation_id == player]
    accuracy = float(_cfg(state).get("enemy_estimate_accuracy", 0.6))
    lines = [
        f"BATTLE: {battle.name.upper()} — WEEK {battle.weeks}",
        f"LOCATION: grid {battle.location[0]:03d}-{battle.location[1]:03d}",
        "",
        "THIS WEEK",
        f"  Our casualties ............. {result.casualties.get(player, 0):,}",
        f"  Enemy casualties ........... {_est(state, battle, enemy_losses, accuracy, 'week')}",
        "",
        "OUR FORMATIONS IN ACTION",
    ]
    lines += [_unit_line(state, u) for u in ours] or ["  None remain in contact."]
    starved = [u.designation for u in result.starved if u.nation_id == player]
    critical = [u.designation for u in ours if u.designation not in starved and _ammo_low(state, u)]
    lines += ["", "AMMUNITION"]
    if starved:
        lines.append(f"  ! Out of ammunition, firing at a fraction of strength: {', '.join(starved)}.")
    if critical:
        lines.append(f"  ! Below a quarter of establishment: {', '.join(critical)}.")
    if not starved and not critical:
        lines.append("  Stocks adequate.")
    ours_support = result.support.get(player, [])
    enemy_support = [line for n, sup in result.support.items() if n != player for line in sup]
    lines += ["", "FIRE SUPPORT"]
    lines += [f"  {line}" for line in ours_support] or [
        "  None. (Artillery 1-2 cells behind the line, warships on BOMBARD offshore, or air superiority.)"]
    if enemy_support:
        lines.append(f"  ! Enemy supporting fire observed ({len(enemy_support)} source(s)).")
    events = []
    events += [f"  {u.designation} has BROKEN and is routing." for u in result.routed if u.nation_id == player]
    events += ["  Enemy formation broken and routing." for u in result.routed if u.nation_id != player]
    events += ["  Enemy formation DESTROYED." for u in result.destroyed if u.nation_id != player]
    events += [f"  {u.designation} has been DESTROYED." for u in result.destroyed if u.nation_id == player]
    events += [f"  {u.designation} is falling back in good order." for u in result.withdrew if u.nation_id == player]
    if events:
        lines += ["", "DEVELOPMENTS", *events]
    lines += ["", "Adjust stances in the War Room (select formation, T). Shells and rounds come only from the "
              "national stockpile: check factory allocations on the Economy screen.", "",
              "— Frontier Army Operations Staff"]
    tpl = state.catalog["generated"]["sitrep"]
    variables = state.text_vars() | {"battle": battle.name, "week": str(battle.weeks)}
    return Email(id="sitrep", sender=fill(tpl["sender"], variables), subject=fill(tpl["subject"], variables),
                 classification=tpl.get("classification", "SECRET"), body="\n".join(lines))


def _ammo_low(state: GameState, unit: Unit) -> bool:
    from src.engine.logistics_engine import fill_ratio

    return fill_ratio(state, unit) < 0.25


def aar_email(state: GameState, battle: Battle) -> Email:
    player = state.player.id
    items = _items(state)
    accuracy = float(_cfg(state).get("aar_estimate_accuracy", 0.75))
    if battle.victor == player:
        outcome = "VICTORY"
    elif battle.victor is None:
        outcome = "INCONCLUSIVE"
    else:
        outcome = "DEFEAT"
    enemy_losses = sum(v for n, v in battle.casualties.items() if n != player)
    lines = [
        f"OPERATION SUMMARY: {battle.name.upper()}",
        f"Grid {battle.location[0]:03d}-{battle.location[1]:03d} · weeks {battle.started_turn:03d}–{state.clock.turn:03d} "
        f"({battle.weeks} week(s) of fighting)",
        "",
        f"OUTCOME ..................... {outcome}",
        f"FIELD HELD BY ............... {state.nations[battle.victor].name if battle.victor else 'NEITHER SIDE'}",
        "",
        "CASUALTIES",
        f"  Kestrian ................... {battle.casualties.get(player, 0):,} (confirmed)",
        f"  Enemy ...................... {_est(state, battle, enemy_losses, accuracy, 'aar')}",
        "",
        "OUR EQUIPMENT LOST",
    ]
    lost = battle.equipment_lost.get(player, {})
    lines += [f"  {items.get(k, {}).get('name', k):<28} {v:>8,}" for k, v in sorted(lost.items()) if v] or ["  None."]
    lines += ["", "OUR AMMUNITION & FUEL EXPENDED"]
    spent = battle.ammo_expended.get(player, {})
    lines += [f"  {items.get(k, {}).get('name', k):<28} {v:>8,}" for k, v in sorted(spent.items()) if v] or ["  None."]
    lines += ["", "OUR FORMATIONS"]
    enemy_broken = 0
    for unit_id in sorted(battle.participants):
        nation_id, label = battle.roster.get(unit_id, ("?", unit_id))
        broke = unit_id in battle.routed or unit_id in battle.destroyed
        if nation_id != player:
            enemy_broken += broke
            continue
        unit = state.unit(unit_id)
        if unit_id in battle.destroyed or unit is None:
            lines.append(f"  {label}: DESTROYED")
            continue
        fate = "ROUTED" if unit_id in battle.routed else ("WITHDREW" if unit_id in battle.withdrew else "HELD")
        lines.append(f"  {label}: {fate}, {unit.strength:,} men remaining")
    lines.append(f"  Enemy formations broken or destroyed: {enemy_broken}")
    lines += ["", "— Operations Directorate, General Staff"]
    tpl = state.catalog["generated"]["aar"]
    variables = state.text_vars() | {"battle": battle.name, "outcome": outcome}
    return Email(id="after_action_report", sender=fill(tpl["sender"], variables),
                 subject=fill(tpl["subject"], variables), classification=tpl.get("classification", "TOP SECRET"),
                 body="\n".join(lines))


# --- tick system ---------------------------------------------------------------------------


class CombatSystem(SimulationSystem):
    """Runs after movement: every engaged group fights a round; battles report and end."""

    name = "combat"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        cfg = _cfg(state)
        for unit in state.all_units():  # routed formations rally after a few weeks
            if unit.status == ROUTING:
                unit.routing_weeks -= 1
                if unit.routing_weeks <= 0:
                    unit.status = HOLDING
                    unit.routing_weeks = 0

        from src.engine.naval_engine import fight_naval_round, is_naval

        results = []
        for group in battle_groups(state):
            battle = _battle_for(state, group)
            naval = all(is_naval(state, state.unit(i)) for i in group if state.unit(i) is not None)
            results.append(fight_naval_round(state, battle, group) if naval else fight_round(state, battle, group))
        state.cost_cache.pop(("support_used", state.clock.turn), None)
        for result in results:
            battle = result.battle
            report.log.append(f"{battle.name}: {result.casualties.get(state.player.id, 0):,} Kestrian casualties.")
            if _still_fighting(state, battle):
                report.new_messages.append(deliver(state, sitrep_email(state, result)))
                continue
            battle.ended_turn = state.clock.turn
            battle.victor = _decide(state, battle)
            if battle.victor == state.player.id:
                state.player.adjust_military_morale(float(cfg.get("victory_military_morale", 3)))
            elif battle.victor is not None:
                state.player.adjust_military_morale(float(cfg.get("defeat_military_morale", -4)))
            report.new_messages.append(deliver(state, aar_email(state, battle)))
            report.log.append(f"{battle.name} has ended.")
        for unit in state.all_units():
            if unit.status == ENGAGED and not unit.engaged_with:
                unit.status = HOLDING
