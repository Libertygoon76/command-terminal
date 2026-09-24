from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MORALE_MIN = 0.0
MORALE_MAX = 100.0

# Upper bounds (exclusive) for each morale band, checked in order.
MORALE_BANDS = (
    (20.0, "COLLAPSING"),
    (40.0, "UNREST"),
    (70.0, "STEADY"),
    (MORALE_MAX + 1, "HIGH"),
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Nation:
    """A sovereign state — the player's or an AI rival's.

    Holds state and enforces trivial invariants only. All simulation math
    (tax income, growth, attrition) belongs in the engine modules.
    """

    id: str
    name: str
    leader_title: str
    capital: str
    treasury: int  # may go negative (debt)
    manpower: int  # recruitable pool, never negative
    morale: float  # 0-100 civilian morale
    population: int
    tax_rate: float  # 0.0-1.0
    stockpiles: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.morale = _clamp(float(self.morale), MORALE_MIN, MORALE_MAX)
        self.tax_rate = _clamp(float(self.tax_rate), 0.0, 1.0)
        self.manpower = max(0, int(self.manpower))

    # --- mutators -----------------------------------------------------------

    def adjust_treasury(self, amount: int) -> None:
        self.treasury += int(amount)

    def adjust_manpower(self, amount: int) -> None:
        self.manpower = max(0, self.manpower + int(amount))

    def adjust_morale(self, delta: float) -> None:
        self.morale = _clamp(self.morale + delta, MORALE_MIN, MORALE_MAX)

    def adjust_stockpile(self, resource_id: str, amount: int) -> None:
        self.stockpiles[resource_id] = max(0, self.stockpiles.get(resource_id, 0) + int(amount))

    # --- derived ------------------------------------------------------------

    @property
    def morale_band(self) -> str:
        for upper, label in MORALE_BANDS:
            if self.morale < upper:
                return label
        return MORALE_BANDS[-1][1]

    @property
    def in_debt(self) -> bool:
        return self.treasury < 0

    # --- serialization ------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Nation:
        return cls(
            id=data["id"],
            name=data["name"],
            leader_title=data.get("leader_title", "Head of State"),
            capital=data.get("capital", ""),
            treasury=int(data["treasury"]),
            manpower=int(data["manpower"]),
            morale=float(data["morale"]),
            population=int(data["population"]),
            tax_rate=float(data["tax_rate"]),
            stockpiles=dict(data.get("stockpiles", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
