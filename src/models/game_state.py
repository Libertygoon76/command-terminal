from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.models.inbox import Email, Inbox
from src.models.nation import Nation


@dataclass
class GameClock:
    start_date: date
    days_per_turn: int = 7
    turn: int = 1

    def date_of(self, turn: int) -> date:
        return self.start_date + timedelta(days=self.days_per_turn * (turn - 1))

    @property
    def current_date(self) -> date:
        return self.date_of(self.turn)

    @property
    def date_str(self) -> str:
        return self.current_date.isoformat()

    def advance(self) -> None:
        self.turn += 1


@dataclass
class ScheduledEmail:
    email_id: str  # template id in GameState.email_library
    turn: int  # turn on which it is delivered


@dataclass
class Ledger:
    """One week of national accounts. Positive amounts; the section says which way money flows."""

    turn: int
    income: dict[str, int] = field(default_factory=dict)
    expenses: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def total_income(self) -> int:
        return sum(self.income.values())

    @property
    def total_expenses(self) -> int:
        return sum(self.expenses.values())

    @property
    def net(self) -> int:
        return self.total_income - self.total_expenses


@dataclass
class GameOver:
    cause: str  # "revolution" | "coup" | "collapse"
    turn: int
    date: str


@dataclass
class GameState:
    """The complete simulation state. Engines mutate it; the UI only reads it."""

    clock: GameClock
    player: Nation
    nations: dict[str, Nation]
    inbox: Inbox = field(default_factory=Inbox)
    email_library: dict[str, Email] = field(default_factory=dict)  # all templates by id
    schedule: list[ScheduledEmail] = field(default_factory=list)  # future deliveries
    config: dict[str, Any] = field(default_factory=dict)
    catalog: dict[str, Any] = field(default_factory=dict)  # static data: resources, units, map, alerts
    flags: dict[str, Any] = field(default_factory=dict)  # story flags set by effects
    rng: random.Random = field(default_factory=random.Random)
    last_ledger: Ledger | None = None
    weeks_insolvent: int = 0  # consecutive weeks with a negative treasury
    game_over: GameOver | None = None

    @property
    def currency(self) -> str:
        return self.config.get("currency", "CR")

    def text_vars(self) -> dict[str, str]:
        """Placeholders available to data-driven text (emails, boot script)."""
        return {
            "nation": self.player.name,
            "leader_title": self.player.leader_title,
            "capital": self.player.capital,
            "designation": self.config.get("terminal_designation", ""),
            "date": self.clock.date_str,
            "turn": str(self.clock.turn),
        }
