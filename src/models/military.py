from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Unit:
    """A military formation on the map.

    `location` is an exact map cell (x = column, y = row) in data/map/world.json coordinates.
    The unit's map symbol (e.g. `[X]`) is centred on that cell, covering x-1 .. x+1.
    """

    id: str
    designation: str  # short tactical code shown in lists, e.g. "K-01"
    name: str
    nation_id: str
    unit_type: str  # template id in units.json
    location: tuple[int, int]
    strength: int  # current manpower
    morale: float = 70.0  # 0-100
    supply: float = 100.0  # 0-100, % of weekly needs met
    stance: str = "trench_warfare"
    commander: str = ""

    @property
    def x(self) -> int:
        return self.location[0]

    @property
    def y(self) -> int:
        return self.location[1]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Unit:
        x, y = data["location"]
        return cls(
            id=data["id"],
            designation=data.get("designation", data["id"]),
            name=data["name"],
            nation_id=data["nation"],
            unit_type=data["type"],
            location=(int(x), int(y)),
            strength=int(data["strength"]),
            morale=float(data.get("morale", 70)),
            supply=float(data.get("supply", 100)),
            stance=data.get("stance", "trench_warfare"),
            commander=data.get("commander", ""),
        )
