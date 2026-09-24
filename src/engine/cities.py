"""Deep city management — the 4X layer (data/cities.json, Cities tab `8`).

Every Kestrian settlement is a CITY (`state.cities`: name -> City) with a population, a LOCAL MORALE and a
CONSTRUCTION QUEUE. Each week (CitySystem):
  * local morale moves morale_pull of the way toward the national civil morale plus local factors: an
    epidemic, a battle nearby, enemy troops in sight, a blockaded harbour, a recent disaster pull it down;
    a hospital, bunkers and new industry lift it. A city below unrest_below drags national morale down;
  * infected cities lose population;
  * the first project in each queue advances a week; finished buildings take effect at once:
      HOSPITAL        outbreaks start there and reach it only a fifth as often (crisis_engine hooks);
      BUNKER COMPLEX  Kestrian formations within 1 row fight at least as if entrenched (combat_engine);
      LOCAL INDUSTRY  +1 military factory for the nation;
  * the LOCAL NEWS WIRE may print a headline chosen by the city's most urgent situation.
Projects are paid for in full when ordered; cancelling the one under way refunds half.
"""

from __future__ import annotations

import random

from src.engine.movement import scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import City, GameState


class CityError(ValueError):
    """An invalid city order."""


def _cfg(state: GameState) -> dict:
    return state.catalog.get("cities", {})


def buildings(state: GameState) -> dict[str, dict]:
    return _cfg(state).get("buildings", {})


def init_cities(state: GameState) -> None:
    """One City per Kestrian settlement; populations vary a little around the type's norm (seeded)."""
    cfg = _cfg(state)
    rng = random.Random(f"{state.intel_seed}:cities")
    world = state.world_map
    for f in world.features:
        owner = world.owner_at(f.x, f.y)
        if owner != state.player.id:
            continue
        base = int(cfg.get("population", {}).get(f.type, 50000))
        state.cities[f.name] = City(name=f.name, type=f.type, nation_id=owner,
                                    population=int(base * rng.uniform(0.85, 1.15)),
                                    local_morale=round(state.player.morale + rng.uniform(-8, 8), 1))


def city_location(state: GameState, city: City) -> tuple[int, int]:
    feature = state.world_map.feature_named(city.name)
    return feature.x, feature.y


def city_region(state: GameState, city: City) -> str:
    return state.world_map.place_name(*city_location(state, city))


def city_at(state: GameState, location: tuple[int, int], radius: float = 1.0) -> list[City]:
    return [c for c in state.cities.values() if scaled_distance(state, city_location(state, c), location) <= radius]


def has_building(state: GameState, city_name: str, building: str) -> bool:
    city = state.cities.get(city_name)
    return city is not None and city.has(building)


# --- construction -------------------------------------------------------------------------------


def order_building(state: GameState, city_name: str, building: str, *, free: bool = False) -> dict:
    """Queue a project in a city, paying for it now (unless an event pays). Returns the project."""
    if state.game_over:
        raise CityError("The government has fallen. The terminal is locked.")
    city = state.cities.get(city_name)
    if city is None:
        raise CityError(f"No Kestrian city named {city_name!r}.")
    spec = buildings(state).get(building)
    if spec is None:
        raise CityError(f"Unknown building {building!r}.")
    if city.count(building) >= int(spec.get("max", 1)):
        raise CityError(f"{city.name} already has (or is building) the maximum of {spec['name']}.")
    nation = state.nations[city.nation_id]
    cost = 0 if free else int(spec["cost"])
    if nation.treasury < cost:
        raise CityError(f"A {spec['name']} costs {cost:,} {state.currency}; the treasury cannot pay.")
    nation.adjust_treasury(-cost)
    project = {"building": building, "weeks_left": int(spec["weeks"]), "weeks_total": int(spec["weeks"]), "cost": cost}
    city.construction_queue.append(project)
    return project


def cancel_building(state: GameState, city_name: str) -> dict:
    """Cancel the last project in the queue (half the cost is refunded)."""
    city = state.cities.get(city_name)
    if city is None or not city.construction_queue:
        raise CityError("Nothing is under construction there.")
    project = city.construction_queue.pop()
    state.nations[city.nation_id].adjust_treasury(int(project["cost"]) // 2)
    return project


def complete(state: GameState, city: City, building: str) -> None:
    city.buildings.append(building)
    if building == "local_industry":
        state.nations[city.nation_id].military_factories += 1
    add_news(state, city, "completed", building=buildings(state)[building]["name"])


# --- bunkers --------------------------------------------------------------------------------------


def bunker_fortification(state: GameState, unit) -> float:
    """Fortification from a Kestrian bunker complex near the unit (1.0 when none)."""
    spec = buildings(state).get("bunker", {})
    radius = float(spec.get("radius", 1.0))
    for city in state.cities.values():
        if city.has("bunker") and city.nation_id == unit.nation_id and \
                scaled_distance(state, city_location(state, city), unit.location) <= radius:
            return float(spec.get("fortification", 1.8))
    return 1.0


# --- morale and news -------------------------------------------------------------------------------


def situation(state: GameState, city: City) -> list[str]:
    """Everything notable about a city this week, most urgent first."""
    from src.engine.weather_engine import is_freezing

    cfg = _cfg(state)
    here = city_location(state, city)
    tags = []
    infection = state.infections.get(f"city:{city.name}")
    if infection:
        tags.append("quarantined" if infection["cure_in"] is not None else "infected")
    radius = float(cfg.get("battle_radius", 4))
    near = [u for u in state.all_units() if state.domain(u) == "land" and scaled_distance(state, u.location, here) <= radius]
    if any(u.engaged for u in near):
        tags.append("battle_near")
    elif any(not state.is_friendly(u.nation_id) for u in near + [u for u in state.visible_hostiles()
                                                                 if scaled_distance(state, u.location, here) <= radius * 1.5]):
        tags.append("enemy_near")
    if city.name in state.blockades:
        tags.append("blockaded")
    if any(scaled_distance(state, c, here) <= 4 for c, kind in state.map_damage.items() if kind.startswith("destroyed")):
        tags.append("disaster")
    if city.construction_queue:
        tags.append("construction")
    if is_freezing(state):
        tags.append("winter")
    if city.local_morale < 30:
        tags.append("low_morale")
    elif city.local_morale > 65:
        tags.append("high_morale")
    return tags


def update_morale(state: GameState, city: City, tags: list[str]) -> None:
    cfg = _cfg(state)
    factors = cfg.get("morale_factors", {})
    target = state.nations[city.nation_id].morale
    target += sum(float(factors.get(t, 0)) for t in tags if t in factors)
    target += sum(float(factors.get(b, 0)) for b in set(city.buildings) if b in factors)
    target = max(0.0, min(100.0, target))
    city.local_morale = round(city.local_morale + (target - city.local_morale) * float(cfg.get("morale_pull", 0.2)), 1)


def add_news(state: GameState, city: City, pool: str, **extra) -> None:
    lines = _cfg(state).get("news", {}).get(pool, [])
    if not lines:
        return
    rng = random.Random(f"{state.intel_seed}:news:{city.name}:{state.clock.turn}:{pool}:{len(city.news)}")
    infection = state.infections.get(f"city:{city.name}")
    disease = ""
    if infection:
        from src.engine.crisis_engine import disease as disease_info

        disease = disease_info(state, infection["disease"])["name"]
    variables = {"city": city.name, "region": city_region(state, city), "disease": disease or "Disease",
                 "nation": state.player.name, "building": extra.get("building", "works")}
    if city.construction_queue and "building" not in extra:
        variables["building"] = buildings(state)[city.construction_queue[0]["building"]]["name"].lower()
    city.news.append({"turn": state.clock.turn, "text": fill(rng.choice(lines), variables)})
    keep = int(_cfg(state).get("news_keep", 12))
    del city.news[:-keep]


def run_city(state: GameState, city: City, report: TickReport) -> None:
    cfg = _cfg(state)
    tags = situation(state, city)
    update_morale(state, city, tags)
    if "infected" in tags or "quarantined" in tags:
        city.population = int(city.population * 0.997)
    if city.construction_queue:
        project = city.construction_queue[0]
        project["weeks_left"] -= 1
        if project["weeks_left"] <= 0:
            city.construction_queue.pop(0)
            complete(state, city, project["building"])
            report.log.append(f"{city.name}: new {buildings(state)[project['building']]['name']} completed.")
            return
    urgent = next((t for t in tags if t in ("infected", "quarantined", "battle_near", "enemy_near", "blockaded",
                                            "disaster")), None)
    rng = random.Random(f"{state.intel_seed}:newsroll:{city.name}:{state.clock.turn}")
    if urgent or rng.random() < float(cfg.get("news_chance", 0.4)):
        pool = urgent or (tags[0] if tags and rng.random() < 0.6 else "quiet")
        add_news(state, city, pool)


class CitySystem(SimulationSystem):
    """Local morale, construction and the news wire in every Kestrian city."""

    name = "cities"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        cfg = _cfg(state)
        unrest = 0
        for city in state.cities.values():
            run_city(state, city, report)
            if city.local_morale < float(cfg.get("unrest_below", 15)):
                unrest += 1
        if unrest:
            state.player.adjust_morale(-float(cfg.get("unrest_morale", 0.25)) * unrest)
            report.log.append(f"UNREST in {unrest} cit{'y' if unrest == 1 else 'ies'}.")
