"""Environmental friction: seasons and weather (data/weather.json), hooked to the GameClock.

Each week the WeatherSystem (first in the tick) rolls one continental condition from the calendar
month's weights; with `persistence` chance last week's condition simply continues if it is possible
this month. The season (Winter Dec–Feb, Spring Mar–May, Summer Jun–Aug, Autumn Sep–Nov) comes from
the date.

  MUD — the Rasputitsa (mostly March–April, October–November): land movement x0.45, armor burns
        2x fuel while moving, supply deliveries x0.8, air power x0.7.
  SNOW / BLIZZARD (Winter): movement slows, air power is cut (a blizzard grounds every wing), and it
        FREEZES. A formation without a Cold-Weather Kit (research: Cold-Weather Equipment) for
        at least 90% of its men loses frost_attrition of its strength and frost_morale morale every
        week to frostbite (only winter_quarters of that if it holds position inside its own supply
        net), and fights at `combat` x its firepower. Kitted formations fight at kitted_combat.
  STORMS at sea (storm_chance by season, highest in winter): warships that are not in port lose
        storm.attrition of their strength (ships founder) and storm.morale, and sail at
        storm.sea_movement.

The FrostSystem applies frostbite after logistics each week; storm damage is applied in the
NavalSystem. The Vosk begin the war already equipped for the cold.
"""

from __future__ import annotations

import math

from src.engine.event_manager import deliver
from src.engine.systems import SimulationSystem, TickReport
from src.models import HOLDING, Email, GameState, Unit

SEASON_ORDER = ("Winter", "Spring", "Summer", "Autumn")


def _data(state: GameState) -> dict:
    return state.catalog.get("weather", {})


def season_of(state: GameState, month: int | None = None) -> str:
    month = month or state.clock.current_date.month
    return _data(state).get("seasons", {}).get(str(month), SEASON_ORDER[(month % 12) // 3])


def condition(state: GameState) -> dict:
    """This week's condition dict (defaults: clear, all factors 1.0)."""
    cond_id = state.weather.get("condition", "clear")
    return _data(state).get("conditions", {}).get(cond_id, {})


def is_freezing(state: GameState) -> bool:
    return bool(condition(state).get("freezing"))


def storm_at_sea(state: GameState) -> bool:
    return bool(state.weather.get("storm"))


def roll_weather(state: GameState) -> dict:
    """Pick this week's weather. Uses the main RNG, so campaigns replay from their seed."""
    data = _data(state)
    month = state.clock.current_date.month
    weights = data.get("months", {}).get(str(month), {"clear": 1})
    previous = state.weather.get("condition")
    if previous in weights and state.rng.random() < float(data.get("persistence", 0.5)):
        cond_id = previous
    else:
        ids = sorted(weights)
        cond_id = state.rng.choices(ids, weights=[weights[i] for i in ids], k=1)[0]
    season = season_of(state, month)
    storm = state.rng.random() < float(data.get("storm_chance", {}).get(season, 0.0))
    return {"condition": cond_id, "season": season, "storm": storm, "turn": state.clock.turn,
            "previous_season": state.weather.get("season")}


def describe(state: GameState) -> str:
    """Short status-bar text, e.g. 'WINTER · SNOW & HARD FROST'."""
    cond = condition(state)
    text = f"{state.weather.get('season', season_of(state)).upper()} · {cond.get('name', 'Clear').upper()}"
    return text + (" · STORMS AT SEA" if storm_at_sea(state) else "")


def movement_factor(state: GameState, unit: Unit) -> float:
    """Weather multiplier on a formation's weekly movement points."""
    if state.domain(unit) == "sea":
        return float(_data(state).get("storm", {}).get("sea_movement", 0.6)) if storm_at_sea(state) else 1.0
    return float(condition(state).get("movement", 1.0))


def air_factor(state: GameState) -> float:
    return float(condition(state).get("air", 1.0))


def winter_kitted(state: GameState, unit: Unit) -> bool:
    return unit.equipment_inventory.get("winter_gear", 0) >= 0.9 * unit.strength


def combat_factor(state: GameState, unit: Unit) -> float:
    """Firepower multiplier from the cold (1.0 unless it is freezing)."""
    if state.domain(unit) != "land" or not is_freezing(state):
        return 1.0
    if winter_kitted(state, unit):
        return float(_data(state).get("kitted_combat", 0.95))
    return float(condition(state).get("combat", 0.75))


def in_winter_quarters(state: GameState, unit: Unit) -> bool:
    """Holding position inside its own supply net: huts, stoves and hot food cut frostbite."""
    return unit.status == HOLDING and unit.supply_state == "supplied"


def apply_frost(state: GameState) -> dict[str, int]:
    """Frostbite for every land formation without winter kit. Returns men lost per nation."""
    losses: dict[str, int] = {}
    if not is_freezing(state):
        return losses
    cond = condition(state)
    rate = float(cond.get("frost_attrition", 0.0))
    morale = float(cond.get("frost_morale", 0.0))
    quarters = float(_data(state).get("winter_quarters", 0.35))
    for unit in state.all_units():
        if state.domain(unit) != "land" or winter_kitted(state, unit):
            continue
        factor = quarters if in_winter_quarters(state, unit) else 1.0
        lost = min(unit.strength - 1, int(math.ceil(unit.strength * rate * factor)))
        if lost > 0:
            unit.strength -= lost
            losses[unit.nation_id] = losses.get(unit.nation_id, 0) + lost
        unit.morale = max(0.0, unit.morale - morale * factor)
    return losses


def bulletin_email(state: GameState) -> Email:
    """METEOROLOGICAL BULLETIN on the first week of a new season (or a freeze)."""
    cond = condition(state)
    tpl = state.catalog["generated"]["weather"]
    lines = [
        f"SEASON: {state.weather['season'].upper()} · CONDITIONS THIS WEEK: {cond.get('name', 'Clear').upper()}",
        "",
        cond.get("description", ""),
        "",
    ]
    season = state.weather["season"]
    if season == "Winter":
        exposed = [u for u in state.player.units if state.domain(u) == "land" and not winter_kitted(state, u)]
        lines += ["WINTER WARNING. Formations without a Cold-Weather Kit for every man will lose men to frostbite each "
                  "week of hard frost, and fight at a fraction of their strength. Formations holding position inside "
                  "our supply net suffer about a third as much.", ""]
        if exposed:
            lines += ["WITHOUT WINTER KIT:", *[f"  • {u.designation} {u.name}" for u in exposed], "",
                      "Research: Cold-Weather Equipment (Research tab), then build Cold-Weather Kits (Economy)."]
        lines += ["", "Storms are frequent at sea: ships outside harbour will suffer."]
    elif season in ("Spring", "Autumn"):
        lines += ["RASPUTITSA. The mud season is upon us. Expect roads to dissolve: movement roughly halved, armor "
                  "burning double fuel, supply convoys delayed. Offensives launched now will bog down."]
    else:
        lines += ["Firm ground and clear skies: the campaigning season. Expect the enemy to use it."]
    lines += ["", "— Central Meteorological Office, Aldmark"]
    variables = state.text_vars() | {"season": season, "condition": cond.get("name", "Clear")}
    return Email(id="weather_bulletin", sender=fill_text(tpl["sender"], variables),
                 subject=fill_text(tpl["subject"], variables), classification=tpl.get("classification", "CONFIDENTIAL"),
                 body="\n".join(lines))


def fill_text(template: str, variables: dict) -> str:
    from src.engine.text import fill

    return fill(template, variables)


class WeatherSystem(SimulationSystem):
    """First in the tick: sets this week's weather."""

    name = "weather"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        state.weather = roll_weather(state)
        report.log.append(f"Weather: {describe(state).title()}.")
        if state.weather["season"] != state.weather.get("previous_season"):
            report.new_messages.append(deliver(state, bulletin_email(state)))


class FrostSystem(SimulationSystem):
    """After logistics: frostbite for formations without winter kit."""

    name = "frost"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        losses = apply_frost(state)
        ours = losses.get(state.player.id, 0)
        if ours:
            report.log.append(f"FROSTBITE: {ours:,} men lost to the cold.")
