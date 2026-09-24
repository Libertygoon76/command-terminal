from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.models.military import Unit

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


def morale_band(value: float) -> str:
    for upper, label in MORALE_BANDS:
        if value < upper:
            return label
    return MORALE_BANDS[-1][1]


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
    morale: float  # 0-100 civilian morale; 0 = revolution
    military_morale: float  # 0-100 armed forces loyalty; too low = coup
    population: int
    tax_rate: float  # 0.0-1.0
    stockpiles: dict[str, int] = field(default_factory=dict)  # raw & manufactured resources (resources.json)
    units: list[Unit] = field(default_factory=list)  # formations in the field
    # --- war economy ---
    military_factories: int = 0
    production: dict[str, int] = field(default_factory=dict)  # equipment id -> factories assigned
    line_efficiency: dict[str, float] = field(default_factory=dict)  # equipment id -> 0..1
    national_stockpile: dict[str, int] = field(default_factory=dict)  # finished equipment in depots
    resource_output: dict[str, int] = field(default_factory=dict)  # resources produced per week
    known_techs: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.morale = _clamp(float(self.morale), MORALE_MIN, MORALE_MAX)
        self.military_morale = _clamp(float(self.military_morale), MORALE_MIN, MORALE_MAX)
        self.tax_rate = _clamp(float(self.tax_rate), 0.0, 1.0)
        self.manpower = max(0, int(self.manpower))

    # --- mutators -----------------------------------------------------------

    def adjust_treasury(self, amount: int) -> None:
        self.treasury += int(amount)

    def adjust_manpower(self, amount: int) -> None:
        self.manpower = max(0, self.manpower + int(amount))

    def adjust_population(self, amount: int) -> None:
        self.population = max(0, self.population + int(amount))

    def adjust_morale(self, delta: float) -> None:
        self.morale = _clamp(self.morale + delta, MORALE_MIN, MORALE_MAX)

    def adjust_military_morale(self, delta: float) -> None:
        self.military_morale = _clamp(self.military_morale + delta, MORALE_MIN, MORALE_MAX)

    def adjust_tax_rate(self, delta: float) -> None:
        self.tax_rate = _clamp(self.tax_rate + delta, 0.0, 1.0)

    def adjust_stockpile(self, resource_id: str, amount: int) -> None:
        self.stockpiles[resource_id] = max(0, self.stockpiles.get(resource_id, 0) + int(amount))

    # --- derived ------------------------------------------------------------

    @property
    def morale_band(self) -> str:
        return morale_band(self.morale)

    @property
    def military_morale_band(self) -> str:
        return morale_band(self.military_morale)

    @property
    def assigned_factories(self) -> int:
        return sum(self.production.values())

    @property
    def free_factories(self) -> int:
        return self.military_factories - self.assigned_factories

    @property
    def total_deployed(self) -> int:
        return sum(u.strength for u in self.units)

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
            military_morale=float(data.get("military_morale", 60)),
            population=int(data["population"]),
            tax_rate=float(data["tax_rate"]),
            stockpiles=dict(data.get("stockpiles", {})),
            military_factories=int(data.get("military_factories", 0)),
            production={k: int(v) for k, v in data.get("production", {}).items() if int(v) > 0},
            national_stockpile={k: int(v) for k, v in data.get("national_stockpile", {}).items()},
            resource_output={k: int(v) for k, v in data.get("resource_output", {}).items()},
            known_techs=set(data.get("known_techs") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
