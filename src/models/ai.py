from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFEND = "DEFEND"
PROBE = "PROBE"
ASSAULT = "ASSAULT"
POSTURES = (DEFEND, PROBE, ASSAULT)


@dataclass
class AIState:
    """Hidden strategic state of an AI-controlled nation. The player never sees this directly."""

    nation_id: str
    posture: str
    tension: float  # 0-100: hostility toward the player; drives escalation
    config: dict[str, Any] = field(default_factory=dict)  # this nation's block from data/ai.json
    weeks_in_posture: int = 0
    focus_y: int | None = None  # Schwerpunkt: the row of the front it is massing toward (PROBE)
    assault_flank: str | None = None  # flank chosen for the current ASSAULT ("north" / "south")
    assault_group: dict[str, int] = field(default_factory=dict)  # unit id -> next waypoint index
    log: list[str] = field(default_factory=list)  # decision history, for debugging/tests

    def __post_init__(self) -> None:
        if self.posture not in POSTURES:
            raise ValueError(f"AI {self.nation_id}: unknown posture {self.posture!r}")
        self.tension = max(0.0, min(100.0, float(self.tension)))

    def adjust_tension(self, delta: float) -> None:
        self.tension = max(0.0, min(100.0, self.tension + delta))
