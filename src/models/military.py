from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HOLDING = "holding"
MOVING = "moving"
ENGAGED = "engaged"


@dataclass
class MoveOrder:
    """Standing order to march to `target` (x, y). The route is re-planned every week."""

    target: tuple[int, int]
    issued_turn: int

    @property
    def x(self) -> int:
        return self.target[0]

    @property
    def y(self) -> int:
        return self.target[1]


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
    # --- orders & status ---
    active_order: MoveOrder | None = None
    move_points: float = 0.0  # unspent movement carried into next week (for slow terrain)
    status: str = HOLDING  # holding | moving | engaged
    supply_state: str = "supplied"  # supplied | overextended | isolated (set by logistics each week)
    engaged_with: list[str] = field(default_factory=list)  # unit ids in contact
    # Physical inventory: equipment id (data/equipment.json) -> count. Placeholder for the future
    # Unit Loadouts system, where combat stats will derive from what the formation actually carries.
    equipment_inventory: dict[str, int] = field(default_factory=dict)

    @property
    def x(self) -> int:
        return self.location[0]

    @property
    def y(self) -> int:
        return self.location[1]

    @property
    def engaged(self) -> bool:
        return self.status == ENGAGED

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
            equipment_inventory={k: int(v) for k, v in data.get("equipment_inventory", {}).items()},
        )


@dataclass
class Contact:
    """Kestrian intelligence's record of a hostile formation it has seen at least once."""

    code: str  # anonymous tracking code assigned at first sighting, e.g. "H-03"
    unit_id: str
    last_x: int
    last_y: int
    last_seen_turn: int
    visible: bool = True
