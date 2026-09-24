from __future__ import annotations

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

    @property
    def current_date(self) -> date:
        return self.start_date + timedelta(days=self.days_per_turn * (self.turn - 1))

    @property
    def date_str(self) -> str:
        return self.current_date.isoformat()

    def advance(self) -> None:
        self.turn += 1


@dataclass
class GameState:
    """The complete simulation state. Engines mutate it; the UI only reads it."""

    clock: GameClock
    player: Nation
    nations: dict[str, Nation]
    inbox: Inbox = field(default_factory=Inbox)
    pending_emails: list[Email] = field(default_factory=list)  # templates not yet delivered
    config: dict[str, Any] = field(default_factory=dict)
    catalog: dict[str, Any] = field(default_factory=dict)  # static data: resources, units, map

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
