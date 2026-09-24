from __future__ import annotations

from collections.abc import Iterable

from src.engine.event_manager import GameOverError
from src.engine.systems import SimulationSystem, TickReport
from src.models import GameState

# --- SCALE -------------------------------------------------------------------------------------
# One turn is one week; one map cell (a row, one grid step) is 10 MILES. Terminal characters are
# twice as tall as they are wide, so a column is half a step (5 miles): config map.column_scale.
# Every speed in the game is authored in miles per day and converted here.
MILES_PER_CELL = 10
DAYS_PER_TURN = 7


def miles_per_cell(state: GameState) -> float:
    return float(state.config.get("map", {}).get("miles_per_cell", MILES_PER_CELL))


def cells_per_turn(state: GameState, miles_per_day: float) -> float:
    """Movement points per week for a pace in miles per day (1 point = one row of open plains)."""
    days = float(state.config.get("days_per_turn", DAYS_PER_TURN))
    return miles_per_day * days / miles_per_cell(state)


def miles(state: GameState, cells: float) -> float:
    return cells * miles_per_cell(state)


class DilemmaPendingError(RuntimeError):
    """Raised when the turn is advanced while a CLASSIFIED DILEMMA still awaits a decision."""


class TickEngine:
    """Advances the simulation one turn (one week) at a time, running systems in a fixed order."""

    miles_per_cell = MILES_PER_CELL
    days_per_turn = DAYS_PER_TURN

    def __init__(self, state: GameState, systems: Iterable[SimulationSystem] = ()) -> None:
        self.state = state
        self.systems: list[SimulationSystem] = list(systems)
        self.miles_per_cell = miles_per_cell(state)
        self.days_per_turn = int(state.config.get("days_per_turn", DAYS_PER_TURN))

    def register(self, system: SimulationSystem) -> None:
        self.systems.append(system)

    def advance(self) -> TickReport:
        if self.state.game_over:
            raise GameOverError("The government has fallen. The terminal is locked.")
        if self.state.pending_dilemma:
            raise DilemmaPendingError("A CLASSIFIED DILEMMA awaits your decision.")
        self.state.clock.advance()
        report = TickReport(turn=self.state.clock.turn, date=self.state.clock.date_str)
        for system in self.systems:
            system.on_tick(self.state, report)
        return report


def build_default_engine(state: GameState) -> TickEngine:
    """Wire up all systems in canonical tick order (see GAME_DESIGN.md §5.3)."""
    from src.engine.ai_director import AIDirector
    from src.engine.air_engine import AirSystem
    from src.engine.combat_engine import CombatSystem
    from src.engine.command import CommandSystem
    from src.engine.crisis_engine import CrisisSystem
    from src.engine.electronic_warfare import EWSystem
    from src.engine.engineering import EngineeringSystem
    from src.engine.dilemmas import DilemmaSystem
    from src.engine.economy_engine import EconomyEngine
    from src.engine.event_manager import EventManager
    from src.engine.fail_states import FailStateSystem
    from src.engine.logistics_engine import LogisticsEngine
    from src.engine.movement import MovementSystem
    from src.engine.naval_engine import NavalSystem
    from src.engine.production import ProductionSystem
    from src.engine.recon import ReconSystem
    from src.engine.recruitment import RecruitmentSystem
    from src.engine.reports import StatusReportSystem
    from src.engine.research import ResearchSystem
    from src.engine.weather_engine import FrostSystem, WeatherSystem

    return TickEngine(
        state,
        [
            WeatherSystem(),  # this week's weather over the continent (and the season)
            CommandSystem(),  # commanders acknowledge last week's orders — or refuse them
            AIDirector(),  # enemy plans its moves (land, sea, air) before the week resolves
            MovementSystem(),  # both sides march and sail simultaneously; clashes detected
            AirSystem(),  # air wings contest their sectors; superiority decided before the fighting
            CombatSystem(),  # land battles (with artillery, naval gunfire, air support) and naval battles
            NavalSystem(),  # blockades imposed and lifted; storms at sea
            ReconSystem(),  # what our forces can now see
            ResearchSystem(),  # labs progress; breakthroughs unlock production and upgrade loadouts
            RecruitmentSystem(),  # training completes; new formations muster
            ProductionSystem(),  # factories deliver to the national stockpile
            LogisticsEngine(),  # supply lines, fuel, resupply from the stockpile, attrition
            EngineeringSystem(),  # combat engineers rebuild wrecked railways and roads
            FrostSystem(),  # frostbite for formations without winter kit
            CrisisSystem(),  # the home front: epidemics, natural disasters, relief duty (CRITICAL EMERGENCY)
            EconomyEngine(),  # taxes, trade (minus blockaded ports), expenses
            EventManager(),  # dispatches, deadlines
            EWSystem(),  # jamming ticks down; last reports of formations still in contact
            StatusReportSystem(),
            FailStateSystem(),  # revolution / coup / collapse — or VICTORY: enemy capitulation
            DilemmaSystem(),  # maybe draw a CLASSIFIED DILEMMA card (pauses the game until answered)
        ],
    )
