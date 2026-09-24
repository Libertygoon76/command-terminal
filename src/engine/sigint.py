"""SIGINT: intercepted radio traffic about enemy orders, pushed as partially redacted dispatches."""

from __future__ import annotations

import random
import re

from src.engine.event_manager import deliver
from src.models import Email, GameState, Unit

REDACTABLE = re.compile(r"\[\[(.+?)\]\]", re.DOTALL)
REDACTED = "[REDACTED]"


def redact(text: str, rng: random.Random, chance: float) -> str:
    """Replace some [[segments]] with [REDACTED]; unwrap the rest."""

    def sub(match: re.Match) -> str:
        return REDACTED if rng.random() < chance else match.group(1)

    return REDACTABLE.sub(sub, text)


def intercept_chance(state: GameState, cfg: dict) -> float:
    chance = float(cfg.get("chance", 0.25))
    for flag, bonus in cfg.get("flag_bonuses", {}).items():
        if state.flags.get(flag):
            chance += float(bonus)
    return min(1.0, chance)


def build_intercept(state: GameState, unit: Unit, target: tuple[int, int], eta_weeks: int, rng: random.Random,
                    cfg: dict) -> Email:
    from src.engine.map_overlay import hostile_label  # avoid import cycle

    tpl = state.catalog["generated"]["sigint"]
    template = next(t for t in state.catalog["units"]["units"] if t["id"] == unit.unit_type)
    role = template.get("role", "infantry")
    error = int(cfg.get("grid_error", 3))
    world = state.world_map
    gx = max(0, min(world.width - 1, target[0] + rng.randint(-error, error)))
    gy = max(0, min(world.height - 1, target[1] + rng.randint(-error // 2, error // 2)))
    region = world.region_at(*target)
    confidence = rng.choice(["LOW", "MODERATE", "MODERATE", "HIGH"])
    variables = state.text_vars() | {
        "station": rng.choice(tpl["stations"]),
        "net": rng.choice(tpl["nets"]),
        "freq": f"{rng.randint(2800, 9900):,}",
        "unit_desc": hostile_label(state, unit, for_intercept=True),
        "role_desc": tpl["role_desc"].get(role, "a formation"),
        "sector": (region.name if region else "the coast").upper(),
        "x": str(gx),
        "y": str(gy),
        "eta": str(max(2, eta_weeks * 7 + rng.randint(-2, 2))),
        "confidence": confidence,
    }
    body = rng.choice(tpl["bodies"]).format_map(variables)
    body = redact(body, rng, float(cfg.get("redaction_chance", 0.35)))
    return Email(
        id="sigint_intercept",
        sender=tpl["sender"].format_map(variables),
        subject=tpl["subject_by_role"].get(role, "SIGINT INTERCEPT — Vosk Movement"),
        classification=tpl.get("classification", "TOP SECRET"),
        body=body,
    )


def is_major_order(state: GameState, unit: Unit, cfg: dict) -> bool:
    """Only major formations (armor, HQ by default) generate enough radio traffic to intercept."""
    template = next(t for t in state.catalog["units"]["units"] if t["id"] == unit.unit_type)
    return template.get("role") in cfg.get("major_roles", ["armor", "hq"])


def maybe_intercept(state: GameState, unit: Unit, target: tuple[int, int], eta_weeks: int,
                    cfg: dict, sent_this_week: int) -> Email | None:
    """Roll for an intercept of a major AI order. Returns the delivered dispatch, if any."""
    if sent_this_week >= int(cfg.get("max_per_week", 2)) or not is_major_order(state, unit, cfg):
        return None
    if state.rng.random() >= intercept_chance(state, cfg):
        return None
    return deliver(state, build_intercept(state, unit, target, eta_weeks, state.rng, cfg))
