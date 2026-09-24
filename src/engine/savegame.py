"""Save / load: the whole campaign as one JSON file (default `savegame.json` in the game folder).

What is saved: every piece of DYNAMIC state in GameState — the clock, both nations (treasury, stockpiles,
factories, research, modifiers, every formation with its inventory, orders, commander and hidden traits),
the inbox (every dispatch, read and reply state), scheduled follow-ups, story flags, the campaign RNG's
exact state, the AI's hidden posture and tension, contacts, engagements, battles, training queues, air
wings, weather, blockades, jammed sectors, map damage, pending dilemmas, and the victory counter.

What is NOT saved (rebuilt from data/ on load): config, the static catalog, email templates, the world map
(the saved `map_damage` is re-applied to it) and derived caches (supply nets, cost tables, intel reports,
which are regenerated deterministically). A loaded campaign therefore continues exactly as the original
would have: the same next week, the same dice.

Encoding: plain JSON, with small tags for what JSON lacks — {"__tuple__": [...]}, {"__set__": [...]},
{"__frozenset__": [...]}, {"__map__": [[key, value], ...]} for dicts with non-string keys, {"__date__": "..."}
and {"__dataclass__": "Name", ...fields} for the model classes.
"""

from __future__ import annotations

import dataclasses
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src import models
from src.models import GameState

SAVE_VERSION = 1
DEFAULT_SAVE = Path(__file__).resolve().parents[2] / "savegame.json"
STATIC_FIELDS = {"config", "catalog", "email_library", "world_map", "cost_cache", "supply_networks", "unit_intel",
                 "player"}
CLASSES = {name: getattr(models, name) for name in models.__all__
           if isinstance(getattr(models, name), type) and dataclasses.is_dataclass(getattr(models, name))}


class SaveError(RuntimeError):
    """A save file that cannot be read or does not match this version of the game."""


# --- codec -----------------------------------------------------------------------------------------


def encode(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        name = type(value).__name__
        if name not in CLASSES:
            raise SaveError(f"cannot save an object of type {name}")
        out = {"__dataclass__": name}
        for f in dataclasses.fields(value):
            if not f.name.startswith("_"):
                out[f.name] = encode(getattr(value, f.name))
        return out
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value):
            return {k: encode(v) for k, v in value.items()}
        return {"__map__": [[encode(k), encode(v)] for k, v in value.items()]}
    if isinstance(value, tuple):
        return {"__tuple__": [encode(v) for v in value]}
    if isinstance(value, frozenset):
        return {"__frozenset__": sorted((encode(v) for v in value), key=repr)}
    if isinstance(value, set):
        return {"__set__": sorted((encode(v) for v in value), key=repr)}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise SaveError(f"cannot save a value of type {type(value).__name__}")


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "__dataclass__" in value:
        cls = CLASSES.get(value["__dataclass__"])
        if cls is None:
            raise SaveError(f"unknown object type {value['__dataclass__']!r} in save file")
        names = {f.name for f in dataclasses.fields(cls) if f.init}
        return cls(**{k: decode(v) for k, v in value.items() if k in names})
    if "__date__" in value:
        return date.fromisoformat(value["__date__"])
    if "__tuple__" in value:
        return tuple(decode(v) for v in value["__tuple__"])
    if "__set__" in value:
        return {decode(v) for v in value["__set__"]}
    if "__frozenset__" in value:
        return frozenset(decode(v) for v in value["__frozenset__"])
    if "__map__" in value:
        return {decode(k): decode(v) for k, v in value["__map__"]}
    return {k: decode(v) for k, v in value.items()}


# --- save / load ----------------------------------------------------------------------------------------


def snapshot(state: GameState) -> dict[str, Any]:
    """The JSON-ready dict of a campaign."""
    fields = {}
    for f in dataclasses.fields(state):
        if f.name in STATIC_FIELDS:
            continue
        value = getattr(state, f.name)
        fields[f.name] = encode(value.getstate() if f.name == "rng" else value)
    return {
        "version": SAVE_VERSION,
        "game": state.config.get("game_title", "COMMAND TERMINAL"),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "turn": state.clock.turn,
        "date": state.clock.date_str,
        "player": state.player.id,
        "state": fields,
    }


def save_game(state: GameState, path: str | Path | None = None) -> Path:
    path = Path(path) if path else DEFAULT_SAVE
    data = snapshot(state)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)  # never leave a half-written save behind
    return path


def restore(data: dict[str, Any]) -> GameState:
    from src.engine.data_loader import new_game
    from src.engine.engineering import apply_damage
    from src.engine.logistics_engine import compute_network

    if data.get("version") != SAVE_VERSION:
        raise SaveError(f"save file version {data.get('version')!r} is not supported (expected {SAVE_VERSION})")
    state = new_game()  # static data (config, catalog, map, templates) and a scaffold to fill
    ai_configs = {nation_id: ai.config for nation_id, ai in state.ai_states.items()}
    saved = data["state"]
    for name, raw in saved.items():
        if not hasattr(state, name) or name in STATIC_FIELDS:
            continue
        value = decode(raw)
        if name == "rng":
            state.rng.setstate(value)
            continue
        setattr(state, name, value)
    state.player = state.nations[data["player"]]
    for nation_id, ai in state.ai_states.items():
        ai.config = ai_configs.get(nation_id, ai.config)
    for battle in state.battles.values():  # defaultdicts do not survive JSON
        battle.casualties = defaultdict(int, battle.casualties)
        for name in ("equipment_lost", "ammo_expended"):
            nested = defaultdict(lambda: defaultdict(int))
            for nation_id, items in getattr(battle, name).items():
                nested[nation_id].update(items)
            setattr(battle, name, nested)
    state.unit_intel = {}
    state.supply_networks = {}
    state.cost_cache = {}
    apply_damage(state)
    for nation_id in state.nations:
        compute_network(state, nation_id)
    return state


def load_game(path: str | Path | None = None) -> GameState:
    path = Path(path) if path else DEFAULT_SAVE
    if not path.exists():
        raise SaveError(f"No saved campaign at {path}.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SaveError(f"{path} is not a valid save file: {error}") from None
    return restore(data)


def describe(path: str | Path | None = None) -> str | None:
    """'WEEK 012 · 1984-03-19 (saved 2026-09-24T21:05:00)' for an existing save, else None."""
    path = Path(path) if path else DEFAULT_SAVE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return f"WEEK {data.get('turn', 0):03d} · {data.get('date', '?')} (saved {data.get('saved_at', '?')})"
