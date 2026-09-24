from __future__ import annotations

from dataclasses import dataclass, field

from src.models import Email, GameOver, GameState


@dataclass
class TickReport:
    """Everything that happened during one turn advance, for the UI to present."""

    turn: int
    date: str
    log: list[str] = field(default_factory=list)
    new_messages: list[Email] = field(default_factory=list)
    game_over: GameOver | None = None


class SimulationSystem:
    """Base class for a module that runs once per tick.

    Called after the clock advances, so `state.clock` already shows the new week;
    each system simulates the week that has just elapsed.
    """

    name: str = "system"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        raise NotImplementedError
