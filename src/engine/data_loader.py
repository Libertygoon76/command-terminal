from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.models import Email, GameClock, GameState, Nation

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_json(relative_path: str, data_dir: Path = DATA_DIR) -> Any:
    path = data_dir / relative_path
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Missing data file: {path}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON in {path}: {e}") from None


def new_game(data_dir: Path = DATA_DIR) -> GameState:
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

    catalog = {
        "resources": load_json("resources.json", data_dir)["resources"],
        "units": load_json("units.json", data_dir),
        "map": load_json("map/world.json", data_dir),
    }

    emails = [Email.from_dict(e) for e in load_json("events/emails.json", data_dir)["emails"]]

    state = GameState(
        clock=clock,
        player=nations[player_id],
        nations=nations,
        pending_emails=sorted(emails, key=lambda e: e.arrives_turn),
        config=config,
        catalog=catalog,
    )
    deliver_due_emails(state)
    return state
