from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


def _nested() -> defaultdict:
    return defaultdict(lambda: defaultdict(int))


@dataclass
class Battle:
    """A multi-week engagement: every formation that has fought in one connected melee.

    Losses and expenditure are true values (the engine's ledger). What the player is told about
    the enemy side is estimated when the SITREP / After Action Report is written.
    """

    id: str
    name: str
    location: tuple[int, int]
    started_turn: int
    participants: set[str] = field(default_factory=set)  # unit ids that have fought here
    roster: dict[str, tuple[str, str]] = field(default_factory=dict)  # unit id -> (nation id, "K-01 name")
    weeks: int = 0
    casualties: dict[str, int] = field(default_factory=lambda: defaultdict(int))  # nation -> men lost
    equipment_lost: dict[str, dict[str, int]] = field(default_factory=_nested)  # nation -> item -> count
    ammo_expended: dict[str, dict[str, int]] = field(default_factory=_nested)  # nation -> item -> count
    routed: list[str] = field(default_factory=list)  # unit ids that broke
    destroyed: list[str] = field(default_factory=list)  # unit ids wiped out
    withdrew: list[str] = field(default_factory=list)  # unit ids that broke contact in good order
    ended_turn: int | None = None
    victor: str | None = None  # nation id, or None for an inconclusive end

    @property
    def active(self) -> bool:
        return self.ended_turn is None
