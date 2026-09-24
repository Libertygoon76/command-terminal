"""Air power, kept abstract: Air Wings are not map units. Each is assigned to a SECTOR (a map region)
or held at base, from the Air Assets screen (6).

Every week, after movement and before the fighting (config `air`):
  * each assigned wing flies with aircraft x fuel ratio x weather air factor (a blizzard grounds
    everything); a patrol burns patrol_fuel_per_aircraft aviation_fuel per aircraft from the
    national stockpile;
  * in each sector the sides' effective air power is compared: SUPERIORITY for the side with at least
    superiority_ratio x the other's (and some aircraft up), DENIED for the other, CONTESTED otherwise;
    where both fly, each loses aircraft = enemy power x attrition_rate x noise;
  * over a sector where a friendly land formation is in battle, the wing also flies ground support:
    support_fuel_per_aircraft more fuel and bombs_per_aircraft bombs per aircraft;
  * a land battle in a sector where one side holds SUPERIORITY gives that side attack_bonus
    firepower (only attack_bonus_no_bombs if its wings had no bombs to drop).
Wings refill (up to replacements_per_week) from the stockpile's strike_aircraft. The AI assigns its
wings to the sectors where its armies are fighting.
"""

from __future__ import annotations

import math

from src.engine.intel import estimate
from src.engine.systems import SimulationSystem, TickReport
from src.models import AirWing, GameState

SUPERIORITY = "SUPERIORITY"
CONTESTED = "CONTESTED"
DENIED = "DENIED"
BASE = "BASE"
GROUNDED = "GROUNDED"


class AirError(ValueError):
    """An invalid air order."""


def _cfg(state: GameState) -> dict:
    return state.config.get("air", {})


def wings(state: GameState, nation_id: str | None = None) -> list[AirWing]:
    return [w for w in state.air_wings if nation_id is None or w.nation_id == nation_id]


def wing(state: GameState, wing_id: str) -> AirWing | None:
    return next((w for w in state.air_wings if w.id == wing_id), None)


def sectors(state: GameState) -> list[str]:
    """Assignable sectors: every land region, in map order (west to east)."""
    world = state.world_map
    return sorted((r.id for r in world.regions.values()),
                  key=lambda rid: (min(x for x, _, _, _ in world.regions[rid].rects), rid))


def assign_wing(state: GameState, wing_id: str, sector: str | None, *, nation_id: str | None = None) -> AirWing:
    if state.game_over:
        raise AirError("The government has fallen. The terminal is locked.")
    target = wing(state, wing_id)
    if target is None or target.nation_id != (nation_id or state.player.id):
        raise AirError(f"No such air wing under your command: {wing_id}")
    if sector is not None and sector not in state.world_map.regions:
        raise AirError(f"Unknown sector {sector!r}")
    target.sector = sector
    target.status = BASE if sector is None else target.status
    return target


def cycle_wing(state: GameState, wing_id: str, step: int) -> AirWing:
    """Move a wing to the next / previous sector in the list (base sits at both ends)."""
    target = wing(state, wing_id)
    if target is None:
        raise AirError(f"No such air wing: {wing_id}")
    order: list[str | None] = [None, *sectors(state)]
    index = order.index(target.sector) if target.sector in order else 0
    return assign_wing(state, wing_id, order[(index + step) % len(order)])


def sector_of(state: GameState, location: tuple[int, int]) -> str | None:
    region = state.world_map.region_at(*location)
    return region.id if region else None


def battles_in(state: GameState, sector: str, nation_id: str) -> bool:
    world = state.world_map
    return any(u.engaged and state.domain(u) == "land" and world.in_region(sector, *u.location)
               for u in state.nations[nation_id].units)


def air_bonus(state: GameState, nation_id: str, location: tuple[int, int]) -> float:
    """Firepower multiplier for `nation_id`'s formations fighting at `location` (1.0 without superiority)."""
    sector = sector_of(state, location)
    picture = state.air_picture.get(sector or "", {})
    return 1.0 + float(picture.get("bonus", {}).get(nation_id, 0.0))


def _draw(nation, item: str, amount: float) -> float:
    """Take up to `amount` of an item from the national stockpile; returns the fraction supplied."""
    if amount <= 0:
        return 1.0
    have = nation.national_stockpile.get(item, 0)
    taken = min(have, int(math.ceil(amount)))
    nation.national_stockpile[item] = have - taken
    return taken / amount


def resolve_air(state: GameState) -> dict[str, dict]:
    """One week of air operations. Returns the air picture per sector (also stored on state)."""
    from src.engine.weather_engine import air_factor

    cfg = _cfg(state)
    rng = state.rng
    weather = air_factor(state)
    picture: dict[str, dict] = {}
    power: dict[str, dict[str, float]] = {}
    bombs_ok: dict[str, dict[str, bool]] = {}
    for w in state.air_wings:
        w.sorties = w.losses = 0
        if w.sector is None:
            w.status = BASE
            continue
        nation = state.nations[w.nation_id]
        supplied = _draw(nation, "aviation_fuel", w.aircraft * float(cfg.get("patrol_fuel_per_aircraft", 1.5)))
        flying = w.aircraft * min(1.0, supplied) * weather
        if flying <= 0.5:
            w.status = GROUNDED
            continue
        if battles_in(state, w.sector, w.nation_id):  # ground-support sorties over our battles
            _draw(nation, "aviation_fuel", flying * float(cfg.get("support_fuel_per_aircraft", 2.5)))
            dropped = _draw(nation, "bombs", flying * float(cfg.get("bombs_per_aircraft", 5)))
            bombs_ok.setdefault(w.sector, {})[w.nation_id] = bombs_ok.get(w.sector, {}).get(w.nation_id, True) and dropped >= 0.5
            w.sorties = int(flying)
        power.setdefault(w.sector, {})
        power[w.sector][w.nation_id] = power[w.sector].get(w.nation_id, 0.0) + flying

    ratio = float(cfg.get("superiority_ratio", 1.5))
    lo, hi = state.config.get("combat", {}).get("noise", [0.8, 1.2])
    for sector, sides in power.items():
        status: dict[str, str] = {}
        bonus: dict[str, float] = {}
        for n, mine in sides.items():
            theirs = sum(v for m, v in sides.items() if m != n)
            if mine > 0 and mine >= ratio * theirs:
                status[n] = SUPERIORITY
                if battles_in(state, sector, n):
                    bonus[n] = float(cfg.get("attack_bonus", 0.3)) if bombs_ok.get(sector, {}).get(n, False) \
                        else float(cfg.get("attack_bonus_no_bombs", 0.1))
            elif theirs >= ratio * mine:
                status[n] = DENIED
            else:
                status[n] = CONTESTED
            if theirs > 0:  # dogfights: losses in proportion to the enemy in the air
                loss_total = theirs * float(cfg.get("attrition_rate", 0.04)) * rng.uniform(lo, hi)
                own_wings = [w for w in state.air_wings if w.sector == sector and w.nation_id == n and w.status != GROUNDED]
                fleet = sum(w.aircraft for w in own_wings) or 1
                for w in own_wings:
                    lost = min(w.aircraft, int(round(loss_total * w.aircraft / fleet)))
                    w.aircraft -= lost
                    w.losses += lost
        for w in state.air_wings:
            if w.sector == sector and w.nation_id in status and w.status != GROUNDED:
                w.status = status[w.nation_id]
        picture[sector] = {"power": dict(sides), "status": status, "bonus": bonus}
    state.air_picture = picture
    return picture


def replenish(state: GameState) -> None:
    per_week = int(_cfg(state).get("replacements_per_week", 6))
    for w in state.air_wings:
        gap = w.establishment - w.aircraft
        if gap > 0:
            nation = state.nations[w.nation_id]
            take = min(gap, per_week, nation.national_stockpile.get("strike_aircraft", 0))
            if take > 0:
                nation.national_stockpile["strike_aircraft"] -= take
                w.aircraft += take


def sector_status(state: GameState, sector: str, nation_id: str) -> str:
    picture = state.air_picture.get(sector)
    if not picture:
        return "—"
    return picture["status"].get(nation_id, DENIED if picture["power"] else "—")


def enemy_air_estimate(state: GameState, sector: str) -> str:
    """What our pilots report of enemy air strength over a sector (fog of war)."""
    import random

    picture = state.air_picture.get(sector, {})
    enemy = sum(v for n, v in picture.get("power", {}).items() if n != state.player.id)
    if not enemy:
        return "none seen"
    rng = random.Random(f"{state.intel_seed}:air:{sector}:{state.clock.turn}")
    reading = estimate(rng, enemy, float(_cfg(state).get("enemy_estimate_accuracy", 0.6)), 0.05)
    return f"{reading.low}–{reading.high} aircraft"


def ai_assign_wings(state: GameState, ai) -> None:
    """Vosk air staff: cover the sectors where the Vosk army is fighting, else the front."""
    nation_id = ai.nation_id
    default = ai.config.get("air", {}).get("default_sector", ai.config.get("front_region"))
    engaged: dict[str, int] = {}
    for u in state.nations[nation_id].units:
        if u.engaged and state.domain(u) == "land":
            sector = sector_of(state, u.location)
            if sector:
                engaged[sector] = engaged.get(sector, 0) + u.strength
    targets = sorted(engaged, key=lambda s: (-engaged[s], s)) or [default]
    for i, w in enumerate(sorted(wings(state, nation_id), key=lambda w: w.id)):
        w.sector = targets[i % len(targets)]


class AirSystem(SimulationSystem):
    name = "air"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        for ai in state.ai_states.values():
            ai_assign_wings(state, ai)
        resolve_air(state)
        replenish(state)
        ours = [w for w in wings(state, state.player.id) if w.sector]
        superior = sorted({state.world_map.regions[w.sector].name for w in ours if w.status == SUPERIORITY})
        lost = sum(w.losses for w in ours)
        if superior:
            report.log.append(f"Air superiority over {', '.join(superior)}.")
        if lost:
            report.log.append(f"{lost} aircraft lost in air combat.")
