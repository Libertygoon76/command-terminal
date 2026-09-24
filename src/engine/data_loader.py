from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path
from typing import Any

from src.engine.effects import validate_effects
from src.models import MISSIONS, AIState, AirWing, Email, GameClock, GameState, Nation, ScheduledEmail, Unit, WorldMap

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

ALERT_CAUSES = ("revolution", "coup", "collapse")


def load_json(relative_path: str, data_dir: Path = DATA_DIR) -> Any:
    path = data_dir / relative_path
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing data file: {path}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON in {path}: {e}") from None


def load_email_library(data_dir: Path = DATA_DIR) -> dict[str, Email]:
    library: dict[str, Email] = {}
    for raw in load_json("events/emails.json", data_dir)["emails"]:
        email = Email.from_dict(raw)
        if email.id in library:
            raise ValueError(f"events/emails.json: duplicate email id {email.id!r}")
        library[email.id] = email
    return library


def validate_email_library(library: dict[str, Email], resource_ids: set[str]) -> None:
    """Catch content mistakes at startup instead of mid-game."""
    for email in library.values():
        where = f"events/emails.json [{email.id}]"
        validate_effects(email.on_arrival, f"{where} on_arrival", resource_ids)
        responses = list(email.options) + ([email.on_expire] if email.on_expire else [])
        option_ids = [o.id for o in email.options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError(f"{where}: duplicate option ids")
        if email.deadline_weeks is not None and not email.options:
            raise ValueError(f"{where}: deadline_weeks set but the email has no options")
        for option in responses:
            validate_effects(option.effects, f"{where} option {option.id!r}", resource_ids)
            for follow_up in option.follow_ups:
                for target in follow_up.email_ids:
                    if target not in library:
                        raise ValueError(f"{where} option {option.id!r}: follow-up email {target!r} does not exist")


def load_orbat(nations: dict[str, Nation], world: WorldMap, units_catalog: dict[str, Any],
               data_dir: Path = DATA_DIR, equipment_ids: set[str] | None = None) -> None:
    """Load starting formations onto their nations, validating every map coordinate (land formations
    on land, warships at sea), and seed each formation's equipment_inventory from its template loadout
    plus the upgrades its nation has already researched (scaled to current strength)."""
    template_by_id = {u["id"]: u for u in units_catalog["units"]}
    templates = set(template_by_id)
    for template in units_catalog["units"]:
        unknown = set(template.get("loadout", {})) - (equipment_ids or set())
        if equipment_ids is not None and unknown:
            raise ValueError(f"units.json [{template['id']}]: unknown equipment in loadout: {sorted(unknown)}")
    stances = {s["id"] for s in units_catalog.get("stances", [])}
    seen: set[str] = set()
    for raw in load_json("orbat.json", data_dir)["units"]:
        unit = Unit.from_dict(raw)
        where = f"orbat.json [{unit.id}]"
        if unit.id in seen:
            raise ValueError(f"{where}: duplicate unit id")
        seen.add(unit.id)
        if unit.nation_id not in nations:
            raise ValueError(f"{where}: unknown nation {unit.nation_id!r}")
        if unit.unit_type not in templates:
            raise ValueError(f"{where}: unknown unit type {unit.unit_type!r}")
        if unit.stance not in stances:
            raise ValueError(f"{where}: unknown stance {unit.stance!r}")
        x, y = unit.location
        if not (1 <= x < world.width - 1 and 0 <= y < world.height):
            raise ValueError(f"{where}: location {unit.location} is off the map (symbol needs x-1..x+1)")
        template = template_by_id[unit.unit_type]
        naval = template.get("domain", "land") == "sea"
        if naval and not world.is_sea(x, y):
            raise ValueError(f"{where}: warship at {unit.location} is on land")
        if not naval and world.is_sea(x, y):
            raise ValueError(f"{where}: location {unit.location} is at sea")
        if unit.mission not in MISSIONS:
            raise ValueError(f"{where}: unknown mission {unit.mission!r}")
        if not unit.equipment_inventory:
            share = unit.strength / max(1, template["manpower"])
            loadout = dict(template.get("loadout", {}))
            for tech_id, extra in template.get("upgrades", {}).items():
                if tech_id in nations[unit.nation_id].known_techs:
                    for item, qty in extra.items():
                        loadout[item] = loadout.get(item, 0) + qty
            unit.equipment_inventory = {k: round(v * share) for k, v in loadout.items()}
        nations[unit.nation_id].units.append(unit)


def load_ai_states(nations: dict[str, Nation], player_id: str, world: WorldMap,
                   data_dir: Path = DATA_DIR) -> dict[str, AIState]:
    states = {}
    for nation_id, cfg in load_json("ai.json", data_dir).items():
        if nation_id.startswith("_"):
            continue
        if nation_id not in nations or nation_id == player_id:
            raise ValueError(f"ai.json: {nation_id!r} is not an AI nation")
        for region_id in [cfg["front_region"], *cfg["home_regions"]]:
            if region_id not in world.regions:
                raise ValueError(f"ai.json [{nation_id}]: unknown region {region_id!r}")
        states[nation_id] = AIState(nation_id, cfg["initial_posture"], cfg["initial_tension"], config=cfg)
    return states


def new_game(data_dir: Path = DATA_DIR, seed: int | None = None) -> GameState:
    """Build a fresh GameState from the JSON data files."""
    from src.engine.event_manager import deliver, deliver_due_emails  # avoid import cycle

    config = load_json("config.json", data_dir)

    nations = {n["id"]: Nation.from_dict(n) for n in load_json("nations.json", data_dir)["nations"]}
    player_id = config["player_nation"]
    if player_id not in nations:
        raise ValueError(f"config.player_nation {player_id!r} not found in nations.json")

    clock = GameClock(
        start_date=date.fromisoformat(config["start_date"]),
        days_per_turn=int(config.get("days_per_turn", 7)),
    )

    alerts = load_json("events/system_alerts.json", data_dir)["alerts"]
    missing = [cause for cause in ALERT_CAUSES if cause not in alerts]
    if missing:
        raise ValueError(f"events/system_alerts.json: missing alerts for {missing}")

    catalog = {
        "resources": load_json("resources.json", data_dir)["resources"],
        "units": load_json("units.json", data_dir),
        "alerts": alerts,
        "generated": load_json("events/generated.json", data_dir),
    }
    world = WorldMap.from_dict(load_json("map/world.json", data_dir))
    catalog["equipment"] = load_json("equipment.json", data_dir)["equipment"]
    catalog["tech_tree"] = load_json("tech_tree.json", data_dir)
    catalog["weather"] = load_json("weather.json", data_dir)
    catalog["events_deck"] = load_json("events_deck.json", data_dir)
    catalog["commanders"] = load_json("commanders.json", data_dir)
    catalog["crises"] = load_json("crises.json", data_dir)
    catalog["diplomacy"] = load_json("diplomacy.json", data_dir)
    catalog["cities"] = load_json("cities.json", data_dir)
    catalog["hotline"] = load_json("hotline.json", data_dir)
    equipment_ids = {e["id"] for e in catalog["equipment"]}
    for nation in nations.values():
        if not nation.known_techs:
            nation.known_techs = set(catalog["tech_tree"].get("known_at_start", []))
        for field_name in ("production", "national_stockpile"):
            unknown = set(getattr(nation, field_name)) - equipment_ids
            if unknown:
                raise ValueError(f"nations.json [{nation.id}] {field_name}: unknown equipment {sorted(unknown)}")
        if nation.assigned_factories > nation.military_factories:
            raise ValueError(f"nations.json [{nation.id}]: more factories assigned than it owns")
        for item_id in nation.production:
            nation.line_efficiency[item_id] = 1.0  # established lines at game start
    policies = {p["id"]: p for p in config.get("economy", {}).get("tax_policies", [])}
    for nation in nations.values():
        if nation.tax_policy not in policies:
            raise ValueError(f"nations.json [{nation.id}]: unknown tax_policy {nation.tax_policy!r}")
        nation.tax_rate = float(policies[nation.tax_policy]["rate"])
    tech_ids = {t["id"] for t in catalog["tech_tree"]["techs"]}
    for template in catalog["units"]["units"]:
        for tech_id, extra in template.get("upgrades", {}).items():
            if tech_id not in tech_ids or set(extra) - equipment_ids:
                raise ValueError(f"units.json [{template['id']}] upgrades: bad tech or equipment in {tech_id!r}")
    load_orbat(nations, world, catalog["units"], data_dir, {e["id"] for e in catalog["equipment"]})

    library = load_email_library(data_dir)
    validate_email_library(library, {r["id"] for r in catalog["resources"]})
    validate_deck(catalog["events_deck"], {r["id"] for r in catalog["resources"]}, equipment_ids)
    for feature in world.ports():
        if feature.harbour and not world.is_sea(*feature.harbour):
            raise ValueError(f"map/world.json: harbour of {feature.name} is not at sea")
    schedule = sorted(
        (ScheduledEmail(e.id, e.arrives_turn) for e in library.values() if e.arrives_turn is not None),
        key=lambda s: s.turn,
    )

    if seed is None:
        seed = config.get("random_seed")

    rng = random.Random(seed)
    state = GameState(
        clock=clock,
        player=nations[player_id],
        nations=nations,
        email_library=library,
        schedule=schedule,
        config=config,
        catalog=catalog,
        rng=rng,
        world_map=world,
        intel_seed=rng.getrandbits(32),
        ai_states=load_ai_states(nations, player_id, world, data_dir),
    )
    for nation_id, ai in state.ai_states.items():
        ai.baseline_strength = sum(u.strength for u in nations[nation_id].units if state.domain(u) == "land")
    state.air_wings = load_air_wings(nations, world, data_dir)
    from src.engine.command import assign_commanders
    from src.engine.electronic_warfare import log_signals

    assign_commanders(state)
    log_signals(state)
    from src.engine.cities import init_cities
    from src.engine.diplomacy import init_foreign

    init_foreign(state)
    init_cities(state)
    from src.engine.weather_engine import bulletin_email, roll_weather

    state.weather = roll_weather(state)  # the campaign opens in the dead of winter
    from src.engine.logistics_engine import compute_network, supply_status
    from src.engine.recon import update_contacts

    update_contacts(state)  # what the front line can see on day one
    for nation_id, nation in nations.items():  # trace day-one supply lines (no consumption yet)
        info = compute_network(state, nation_id)
        for unit in nation.units:
            unit.supply_state = supply_status(state, unit, info)[0]
    deliver_due_emails(state)
    deliver(state, bulletin_email(state))
    return state


def load_air_wings(nations: dict[str, Nation], world: WorldMap, data_dir: Path = DATA_DIR) -> list[AirWing]:
    wings = []
    for raw in load_json("air.json", data_dir)["wings"]:
        where = f"air.json [{raw['id']}]"
        if raw["nation"] not in nations:
            raise ValueError(f"{where}: unknown nation {raw['nation']!r}")
        if raw.get("sector") and raw["sector"] not in world.regions:
            raise ValueError(f"{where}: unknown sector {raw['sector']!r}")
        wings.append(AirWing(id=raw["id"], name=raw["name"], nation_id=raw["nation"], aircraft=int(raw["aircraft"]),
                             establishment=int(raw.get("establishment", 48)), sector=raw.get("sector")))
    return wings


def validate_deck(deck: dict[str, Any], resource_ids: set[str], equipment_ids: set[str]) -> None:
    seen = set()
    for card in deck.get("cards", []):
        where = f"events_deck.json [{card.get('id')}]"
        if card["id"] in seen:
            raise ValueError(f"{where}: duplicate card id")
        seen.add(card["id"])
        choices = card.get("choices", [])
        if not (1 if card.get("chain_only") else 2) <= len(choices) <= 3:
            raise ValueError(f"{where}: a dilemma needs 2 or 3 choices (a chain-only card may have 1)")
        if len({c["id"] for c in choices}) != len(choices):
            raise ValueError(f"{where}: duplicate choice ids")
        for choice in choices:
            validate_effects(choice.get("effects", {}), f"{where} choice {choice['id']!r}", resource_ids, equipment_ids)
    ids = {c["id"] for c in deck.get("cards", [])}
    for card in deck.get("cards", []):  # event chains must point at cards that exist
        for choice in card.get("choices", []):
            for follow in choice.get("follow_ups", []):
                targets = [follow["card"]] if "card" in follow else [o["card"] for o in follow.get("outcomes", [])]
                missing = [t for t in targets if t not in ids]
                if missing or not targets:
                    raise ValueError(f"events_deck.json [{card['id']}] choice {choice['id']!r}: follow-up to unknown "
                                     f"card(s) {missing or '(none)'}")
