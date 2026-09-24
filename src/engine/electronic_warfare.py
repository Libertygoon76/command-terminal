"""Electronic warfare: jamming and LOSS OF SIGNAL.

A JAMMING ZONE (`state.jammed`: zone id -> {center, radius, weeks, sector}) is a circle of `radius` rows
(10 miles each) around a point, named after the map sector it falls in. Every friendly LAND formation
inside it is cut off from the terminal:
  * it drops off the Order of Battle; its map symbol becomes `[?]` at its last reported position
    (`state.signal_log`), labelled CONTACT LOST;
  * it cannot be given orders, stances or a new commander, and its strength, supply and morale are unknown;
  * what it sees no longer reaches the staff (it stops spotting for the fog of war);
  * it keeps fighting and following its last orders; SITREPs from its battles say NO REPORT.
The Vosk AI jams (ai.json `ew`: chance by posture, 1–3 weeks, centred on the heaviest concentration of
Kestrian troops). Natural interference (config electronic_warfare.random_chance) can black out a smaller
zone around one formation for a week. The EWSystem runs at the end of each week: zones tick down, the
last reports of formations still in contact are logged, and LOSS OF SIGNAL / SIGNAL RESTORED dispatches go out.
"""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.movement import OrderError, scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import Email, GameState, Unit


def zone_of(state: GameState, unit: Unit) -> str | None:
    """The jamming zone blacking this friendly land formation out, if any."""
    if not state.is_friendly(unit.nation_id) or state.domain(unit) != "land":
        return None
    for zone_id, zone in sorted(state.jammed.items()):
        if scaled_distance(state, unit.location, tuple(zone["center"])) <= float(zone["radius"]):
            return zone_id
    return None


def is_dark(state: GameState, unit: Unit) -> bool:
    return zone_of(state, unit) is not None


def dark_units(state: GameState) -> list[Unit]:
    return [u for u in state.player.units if is_dark(state, u)]


def reachable_units(state: GameState) -> list[Unit]:
    return [u for u in state.player.units if not is_dark(state, u)]


def zone_name(state: GameState, zone: dict) -> str:
    x, y = zone["center"]
    return f"{zone['sector']} around grid {x:03d}-{y:03d}"


def require_signal(state: GameState, unit: Unit) -> None:
    zone_id = zone_of(state, unit)
    if zone_id is not None:
        raise OrderError(f"NO SIGNAL: {unit.designation} cannot be reached — the enemy is jamming "
                         f"{zone_name(state, state.jammed[zone_id])}.")


def last_report(state: GameState, unit: Unit) -> dict:
    return state.signal_log.get(unit.id, {"x": unit.x, "y": unit.y, "turn": state.clock.turn})


def log_signals(state: GameState) -> None:
    """Record the last report of every formation still in contact."""
    for unit in state.player.units:
        if not is_dark(state, unit):
            state.signal_log[unit.id] = {"x": unit.x, "y": unit.y, "turn": state.clock.turn,
                                         "strength": unit.strength, "supply": round(unit.supply)}
    for unit_id in [i for i in state.signal_log if state.unit(i) is None]:
        del state.signal_log[unit_id]


def jam(state: GameState, center: tuple[int, int], radius: float, weeks: int) -> tuple[str, list[Unit]]:
    """Open a jamming zone. Returns (zone id, the friendly formations that go dark)."""
    x, y = int(center[0]), int(center[1])
    zone_id = f"JZ-{x:03d}-{y:03d}"
    region = state.world_map.region_at(x, y)
    zone = state.jammed.setdefault(zone_id, {"center": [x, y], "radius": float(radius), "weeks": 0,
                                             "sector": region.name if region else state.world_map.place_name(x, y)})
    zone["weeks"] = max(int(weeks), int(zone["weeks"]))
    return zone_id, [u for u in state.player.units if zone_of(state, u) == zone_id]


def signal_email(state: GameState, zone: dict, lost: bool, units: list[Unit]) -> Email:
    tpl = state.catalog["generated"]["signal_lost" if lost else "signal_restored"]
    name = zone_name(state, zone)
    if lost:
        lines = [
            f"All radio and landline traffic from {name} (about {zone['radius'] * 10:.0f} miles around) has been "
            "lost. The spectrum is saturated with broadband noise: the enemy is jamming.",
            "",
            "FORMATIONS OUT OF CONTACT:",
            *[f"  • {u.designation} {u.name} — last reported at grid {last_report(state, u)['x']:03d}-"
              f"{last_report(state, u)['y']:03d}" for u in units],
            "",
            "They will carry out their last orders. We cannot change those orders, see their strength or supply, "
            "or hear what they see until the jamming stops. Expect one to three weeks.",
        ]
    else:
        lines = [f"Communications with {name} are restored. Formations there report in: see the War Room."]
    lines += ["", "— Signals Directorate"]
    variables = state.text_vars() | {"sector": name.upper()}
    return Email(id="signal_lost" if lost else "signal_restored", sender=fill(tpl["sender"], variables),
                 subject=fill(tpl["subject"], variables), classification=tpl.get("classification", "SECRET"),
                 body="\n".join(lines))


def start_jam(state: GameState, center: tuple[int, int], radius: float, weeks: int, report: TickReport) -> str:
    zone_id, lost = jam(state, center, radius, weeks)
    if lost:
        report.new_messages.append(deliver(state, signal_email(state, state.jammed[zone_id], True, lost)))
        report.log.append(f"LOSS OF SIGNAL: {zone_name(state, state.jammed[zone_id])}.")
    return zone_id


def heaviest_concentration(state: GameState, radius: float) -> tuple[int, int] | None:
    """The friendly land formation with the most Kestrian strength within `radius` of it."""
    land = [u for u in state.player.units if state.domain(u) == "land"]
    if not land:
        return None
    best = max(land, key=lambda u: (sum(o.strength for o in land if scaled_distance(state, o.location, u.location)
                                        <= radius), u.id))
    return best.location


def ai_jamming(state: GameState, ai, report: TickReport) -> str | None:
    """The Vosk electronic-warfare brigade picks a target to blind (called by the AI Director)."""
    cfg = ai.config.get("ew", {})
    if len(state.jammed) >= int(cfg.get("max_sectors", 1)):
        return None
    if state.rng.random() >= float(cfg.get("chance", {}).get(ai.posture, 0.0)):
        return None
    radius = float(cfg.get("radius", 7))
    center = heaviest_concentration(state, radius)
    if center is None:
        return None
    lo, hi = cfg.get("weeks", [1, 3])
    # +1: this week's end-of-turn countdown happens before the player ever sees the blackout.
    return start_jam(state, center, radius, state.rng.randint(int(lo), int(hi)) + 1, report)


class EWSystem(SimulationSystem):
    """End of the week: natural interference, jams tick down, last reports logged."""

    name = "electronic_warfare"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        cfg = state.config.get("electronic_warfare", {})
        for zone_id in sorted(state.jammed):
            zone = state.jammed[zone_id]
            zone["weeks"] -= 1
            if zone["weeks"] <= 0:
                units = [u for u in state.player.units if zone_of(state, u) == zone_id]
                del state.jammed[zone_id]
                if units:
                    report.new_messages.append(deliver(state, signal_email(state, zone, False, units)))
                report.log.append(f"Signal restored: {zone_name(state, zone)}.")
        if not state.jammed and state.rng.random() < float(cfg.get("random_chance", 0.0)):
            land = sorted((u for u in state.player.units if state.domain(u) == "land"), key=lambda u: u.id)
            if land:
                lo, hi = cfg.get("random_weeks", [1, 1])
                start_jam(state, state.rng.choice(land).location, float(cfg.get("random_radius", 4)),
                          state.rng.randint(int(lo), int(hi)), report)
        log_signals(state)
