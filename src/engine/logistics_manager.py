from __future__ import annotations

from src.engine.systems import SimulationSystem, TickReport
from src.models import GameState


class LogisticsManager(SimulationSystem):
    """STUB (Phase 5): supply distribution along supply lines, army consumption, attrition, desertion."""

    name = "logistics"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        pass
