"""Fog of war: turns true values into uncertain intelligence estimates.

An intel spec in an email template looks like:
    "intel": {
      "vosk_frontier": {"value": 52000, "accuracy": 0.45},
      "vosk_army":     {"nation": "vosk", "stat": "manpower", "fraction": 0.6, "accuracy": 0.7}
    }
and the body references it as {vosk_frontier}. The rendered text is a range plus a stated
certainty. Reports can be flatly wrong: with a chance that grows as accuracy falls, the
estimate is centred on a false value while still claiming the same certainty.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from src.models import GameState, Unit


@dataclass(frozen=True)
class IntelReading:
    low: int
    high: int
    certainty: float
    true_value: float
    misinformation: bool

    @property
    def text(self) -> str:
        return f"{self.low:,} – {self.high:,} (CERTAINTY {self.certainty:.0%})"


def _round_nice(value: float) -> int:
    """Round to two significant figures, as an analyst would."""
    if value <= 0:
        return 0
    magnitude = 10 ** max(0, int(math.floor(math.log10(value))) - 1)
    return int(round(value / magnitude) * magnitude)


def estimate(rng: random.Random, true_value: float, accuracy: float, misinformation_chance: float) -> IntelReading:
    accuracy = max(0.05, min(0.99, accuracy))
    misinformation = rng.random() < misinformation_chance
    if misinformation:
        # Deception, bad sources, or a double agent: the whole picture is off.
        factor = rng.uniform(0.25, 0.6) if rng.random() < 0.5 else rng.uniform(1.6, 3.0)
        center = true_value * factor
    else:
        center = true_value * (1 + rng.gauss(0, (1 - accuracy) * 0.35))
    half_width = max(1.0, center * (1 - accuracy) * 0.6)
    low = _round_nice(max(0.0, center - half_width))
    high = max(low, _round_nice(center + half_width))
    return IntelReading(low, high, accuracy, true_value, misinformation)


def resolve_true_value(state: GameState, spec: dict[str, Any]) -> float:
    if "value" in spec:
        return float(spec["value"])
    nation = state.nations[spec["nation"]]
    return float(getattr(nation, spec["stat"])) * float(spec.get("fraction", 1.0))


def generate_readings(state: GameState, specs: dict[str, dict[str, Any]]) -> dict[str, IntelReading]:
    base_chance = float(state.config.get("intel", {}).get("misinformation_base_chance", 0.15))
    readings = {}
    for name, spec in specs.items():
        accuracy = float(spec.get("accuracy", 0.5))
        chance = float(spec.get("misinformation_chance", base_chance * (1 - accuracy) * 2))
        readings[name] = estimate(state.rng, resolve_true_value(state, spec), accuracy, chance)
    return readings


# --- enemy formations on the map ----------------------------------------------


@dataclass(frozen=True)
class UnitIntel:
    """What Kestrian intelligence believes about a hostile formation this week.

    Rolled once per unit per week from a seed of (campaign, unit, week): looking at the
    map never disturbs the main RNG, and re-inspecting a unit cannot be used to average
    estimates toward the truth. The report changes only when a new week begins.
    """

    turn: int
    accuracy: float
    strength: IntelReading
    reported_type: str  # may differ from the real type (misidentification)
    identified: bool  # whether the formation's designation is known
    misidentified: bool


def recon_accuracy(state: GameState, unit: Unit) -> float:
    cfg = state.config.get("intel", {}).get("unit_recon", {})
    accuracy = float(cfg.get("base_accuracy", 0.45))
    frontline = int(cfg.get("frontline_range", 12))
    friendly = state.player.units
    if any(abs(f.x - unit.x) + abs(f.y - unit.y) <= frontline for f in friendly):
        accuracy += float(cfg.get("frontline_bonus", 0.15))
    for flag, bonus in cfg.get("flag_bonuses", {}).items():
        if state.flags.get(flag):
            accuracy += float(bonus)
    return min(float(cfg.get("max_accuracy", 0.9)), accuracy)


def unit_report(state: GameState, unit: Unit) -> UnitIntel:
    cached = state.unit_intel.get(unit.id)
    if cached is not None and cached.turn == state.clock.turn:
        return cached

    cfg = state.config.get("intel", {})
    rng = random.Random(f"{state.intel_seed}:{unit.id}:{state.clock.turn}")
    accuracy = recon_accuracy(state, unit)
    base_chance = float(cfg.get("misinformation_base_chance", 0.15))
    reading = estimate(rng, unit.strength, accuracy, base_chance * (1 - accuracy) * 2)

    # A formation we can name, we can also type. Only unidentified contacts get misclassified.
    identified = rng.random() < accuracy
    domain = state.domain(unit)  # a warship is never mistaken for a tank, nor the reverse
    templates = [u["id"] for u in state.catalog["units"]["units"] if u.get("domain", "land") == domain]
    misidentify = not identified and rng.random() < (1 - accuracy) * float(
        cfg.get("unit_recon", {}).get("misidentify_factor", 0.35)
    )
    reported_type = unit.unit_type
    if misidentify:
        reported_type = rng.choice([t for t in templates if t != unit.unit_type])

    report = UnitIntel(
        turn=state.clock.turn,
        accuracy=accuracy,
        strength=reading,
        reported_type=reported_type,
        identified=identified,
        misidentified=misidentify,
    )
    state.unit_intel[unit.id] = report
    return report
