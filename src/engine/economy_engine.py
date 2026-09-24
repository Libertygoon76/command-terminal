from __future__ import annotations

from src.engine.systems import SimulationSystem, TickReport
from src.models import GameState


class EconomyEngine(SimulationSystem):
    """STUB (Phase 3): tax income, production chains, consumption, market prices, upkeep."""

    name = "economy"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        pass
