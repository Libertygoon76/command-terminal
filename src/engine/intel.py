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

from src.models import GameState


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
