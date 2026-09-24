from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.models.inbox import Email, Inbox
from src.models.ai import AIState
from src.models.military import Contact, Unit
from src.models.nation import Nation
from src.models.world_map import WorldMap


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
    world_map: WorldMap | None = None
    unit_intel: dict[str, Any] = field(default_factory=dict)  # unit id -> cached fog-of-war report
    intel_seed: int = 0  # seeds per-unit weekly intel rolls independently of `rng`
    ai_states: dict[str, AIState] = field(default_factory=dict)  # nation id -> hidden AI state
    contacts: dict[str, Contact] = field(default_factory=dict)  # hostile unit id -> what we know of it
    engagements: set[frozenset[str]] = field(default_factory=set)  # unit-id pairs currently in contact
    supply_networks: dict[str, dict[str, Any]] = field(default_factory=dict)  # nation id -> last traced supply net
    cost_cache: dict[Any, Any] = field(default_factory=dict, repr=False)  # derived static data (e.g. supply costs)

    def all_units(self) -> list[Unit]:
        return [unit for nation in self.nations.values() for unit in nation.units]

    def unit(self, unit_id: str) -> Unit | None:
        return next((u for u in self.all_units() if u.id == unit_id), None)

    def is_friendly(self, nation_id: str) -> bool:
        return nation_id == self.player.id

    def hostile_units(self) -> list[Unit]:
        return [u for u in self.all_units() if not self.is_friendly(u.nation_id)]

    def visible_hostiles(self) -> list[Unit]:
        """Hostile formations currently observed by Kestrian forces (or all, in reveal/debug mode)."""
        if self.config.get("map", {}).get("debug_reveal_all"):
            return self.hostile_units()
        return [u for u in self.hostile_units() if (c := self.contacts.get(u.id)) is not None and c.visible]

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
