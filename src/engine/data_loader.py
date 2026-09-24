from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path
from typing import Any

from src.engine.effects import validate_effects
from src.models import Email, GameClock, GameState, Nation, ScheduledEmail, Unit, WorldMap

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
               data_dir: Path = DATA_DIR) -> None:
    """Load starting formations onto their nations, validating every map coordinate."""
    templates = {u["id"] for u in units_catalog["units"]}
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
        if world.is_sea(x, y):
            raise ValueError(f"{where}: location {unit.location} is at sea")
        nations[unit.nation_id].units.append(unit)


def new_game(data_dir: Path = DATA_DIR, seed: int | None = None) -> GameState:
    """Build a fresh GameState from the JSON data files."""
    from src.engine.event_manager import deliver_due_emails  # avoid import cycle

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
    }
    world = WorldMap.from_dict(load_json("map/world.json", data_dir))
    load_orbat(nations, world, catalog["units"], data_dir)

    library = load_email_library(data_dir)
    validate_email_library(library, {r["id"] for r in catalog["resources"]})
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
    )
    deliver_due_emails(state)
    return state
