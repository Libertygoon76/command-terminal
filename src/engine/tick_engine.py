from __future__ import annotations

from collections.abc import Iterable

from src.engine.event_manager import GameOverError
from src.engine.systems import SimulationSystem, TickReport
from src.models import GameState


class TickEngine:
    """Advances the simulation one turn at a time, running systems in a fixed order."""

    def __init__(self, state: GameState, systems: Iterable[SimulationSystem] = ()) -> None:
        self.state = state
        self.systems: list[SimulationSystem] = list(systems)

    def register(self, system: SimulationSystem) -> None:
        self.systems.append(system)

    def advance(self) -> TickReport:
        if self.state.game_over:
            raise GameOverError("The government has fallen. The terminal is locked.")
        self.state.clock.advance()
        report = TickReport(turn=self.state.clock.turn, date=self.state.clock.date_str)
        for system in self.systems:
            system.on_tick(self.state, report)
        return report


def build_default_engine(state: GameState) -> TickEngine:
    """Wire up all systems in canonical tick order (see GAME_DESIGN.md §5.3)."""
    from src.engine.economy_engine import EconomyEngine
    from src.engine.event_manager import EventManager
    from src.engine.fail_states import FailStateSystem
    from src.engine.logistics_manager import LogisticsManager
    from src.engine.reports import StatusReportSystem

    return TickEngine(
        state,
        [EconomyEngine(), LogisticsManager(), EventManager(), StatusReportSystem(), FailStateSystem()],
    )
