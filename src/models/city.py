from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class City:
    """A living settlement (Expansion 1.1): the local layer under the national statistics.

    `name` matches a world.json feature. Population and local morale move every week (src/engine/cities.py);
    `buildings` are finished structures, `construction_queue` the projects paid for and under way (the first
    one is being built), and `news` the local wire service: [{turn, text}], newest last.
    """

    name: str
    type: str  # capital | city | industrial | port | town
    nation_id: str
    population: int
    local_morale: float
    buildings: list[str] = field(default_factory=list)
    construction_queue: list[dict[str, Any]] = field(default_factory=list)  # {building, weeks_left, weeks_total, cost}
    news: list[dict[str, Any]] = field(default_factory=list)

    def has(self, building: str) -> bool:
        return building in self.buildings

    def count(self, building: str) -> int:
        return self.buildings.count(building) + sum(1 for p in self.construction_queue if p["building"] == building)
