"""Effect interpreter: turns JSON effect dicts into changes on GameState.

Supported keys (all deltas):
    treasury, manpower, population       int
    morale, military_morale               float (points on the 0-100 scale)
    tax_rate                              float (fraction, 0.02 = +2 percentage points)
    stockpiles                            {resource_id: int}
    flags                                 {flag_name: value}   (set, not added)
    ai_tension                            float, applied to every AI nation's hidden tension.
                                          Never shown to the player: they must infer it.
"""

from __future__ import annotations

from typing import Any

from src.models import GameState

# effect key -> (display label, Nation adjust method, display format)
NATION_EFFECTS: dict[str, tuple[str, str, str]] = {
    "treasury": ("TREASURY", "adjust_treasury", "money"),
    "manpower": ("MANPOWER", "adjust_manpower", "int"),
    "population": ("POPULATION", "adjust_population", "int"),
    "morale": ("CIVIL MORALE", "adjust_morale", "points"),
    "military_morale": ("MILITARY MORALE", "adjust_military_morale", "points"),
    "tax_rate": ("TAX RATE", "adjust_tax_rate", "rate"),
}
SPECIAL_EFFECTS = {"stockpiles", "flags", "ai_tension"}
VALID_EFFECT_KEYS = set(NATION_EFFECTS) | SPECIAL_EFFECTS


def validate_effects(effects: dict[str, Any], where: str, resource_ids: set[str]) -> None:
    """Raise ValueError with a designer-friendly message for malformed effect blocks."""
    for key, value in effects.items():
        if key not in VALID_EFFECT_KEYS:
            raise ValueError(f"{where}: unknown effect {key!r} (valid: {sorted(VALID_EFFECT_KEYS)})")
        if key == "stockpiles":
            for rid in value:
                if rid not in resource_ids:
                    raise ValueError(f"{where}: unknown resource {rid!r} in stockpiles effect")
        elif key != "flags" and not isinstance(value, (int, float)):
            raise ValueError(f"{where}: effect {key!r} must be a number, got {value!r}")


def _sign(delta: float) -> str:
    return "+" if delta >= 0 else "−"


def describe(label: str, delta: float, fmt: str, currency: str, unit: str = "") -> str:
    magnitude = abs(delta)
    if fmt == "money":
        value = f"{magnitude:,.0f} {currency}"
    elif fmt == "points":
        value = f"{magnitude:g}"
    elif fmt == "rate":
        value = f"{magnitude * 100:.1f} pts"
    else:
        value = f"{magnitude:,.0f}{(' ' + unit) if unit else ''}"
    return f"{label} {_sign(delta)}{value}"


def apply_effects(state: GameState, effects: dict[str, Any]) -> list[str]:
    """Apply an effect block to the player nation. Returns human-readable change lines."""
    nation = state.player
    changes: list[str] = []
    for key, value in effects.items():
        if key in NATION_EFFECTS:
            if not value:
                continue
            label, method, fmt = NATION_EFFECTS[key]
            getattr(nation, method)(value)
            changes.append(describe(label, value, fmt, state.currency))
        elif key == "stockpiles":
            resources = {r["id"]: r for r in state.catalog.get("resources", [])}
            for rid, amount in value.items():
                if not amount:
                    continue
                nation.adjust_stockpile(rid, amount)
                res = resources.get(rid, {})
                changes.append(describe(res.get("name", rid).upper(), amount, "int", state.currency, res.get("unit", "")))
        elif key == "flags":
            state.flags.update(value)
        elif key == "ai_tension":
            for ai in state.ai_states.values():
                ai.adjust_tension(value)
    return changes
