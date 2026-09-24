from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HOLDING = "holding"
MOVING = "moving"
ENGAGED = "engaged"
ROUTING = "routing"

DEFEND = "defend"
ASSAULT = "assault"
WITHDRAW = "withdraw"
STANCES = (DEFEND, ASSAULT, WITHDRAW)

# Naval missions (warships only; see naval_engine).
PATROL = "patrol"  # hold station, engage enemy fleets that come into contact
BLOCKADE = "blockade"  # close enemy ports within naval.blockade_range: no trade, no supply from them
BOMBARD = "bombard"  # shell the coast: fire support for friendly land battles within naval.bombard_range
MISSIONS = (PATROL, BLOCKADE, BOMBARD)


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
    stance: str = DEFEND  # combat stance: defend | assault | withdraw
    mission: str = PATROL  # warships only: patrol | blockade | bombard
    commander: str = ""
    traits: list[str] = field(default_factory=list)  # hidden commander traits (data/commanders.json)
    traits_known: bool = False  # revealed by a refusal or the commander's first week in battle
    pending_orders: list[str] = field(default_factory=list)  # orders not yet acknowledged: "stance", "move"
    relief_weeks: int = 0  # weeks left on disaster relief duty (cannot move or fight)
    # --- orders & status ---
    active_order: MoveOrder | None = None
    move_points: float = 0.0  # unspent movement carried into next week (for slow terrain)
    status: str = HOLDING  # holding | moving | engaged | routing
    routing_weeks: int = 0  # weeks left before a routed unit rallies
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

    @property
    def routing(self) -> bool:
        return self.status == ROUTING

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
            stance=data.get("stance", DEFEND),
            mission=data.get("mission", PATROL),
            commander=data.get("commander", ""),
            equipment_inventory={k: int(v) for k, v in data.get("equipment_inventory", {}).items()},
        )


@dataclass
class AirWing:
    """An abstract air wing (not a map unit). Assigned to a map sector (region) it contests the
    skies there; with air superiority it supports every friendly land battle in the sector."""

    id: str
    name: str
    nation_id: str
    aircraft: int
    establishment: int = 48
    sector: str | None = None  # region id, or None = held at base
    status: str = "BASE"  # last week's outcome in its sector: SUPERIORITY | CONTESTED | DENIED | BASE | GROUNDED
    sorties: int = 0  # support missions flown last week
    losses: int = 0  # aircraft lost last week


@dataclass
class TrainingOrder:
    """A formation being raised: paid for up front, it musters when `weeks_left` reaches 0."""

    id: str  # future unit id
    nation_id: str
    unit_type: str
    designation: str
    name: str
    weeks_total: int
    weeks_left: int
    cost: int
    manpower: int
    started_turn: int

    @property
    def progress(self) -> float:
        return 1.0 - self.weeks_left / max(1, self.weeks_total)


@dataclass
class Contact:
    """Kestrian intelligence's record of a hostile formation it has seen at least once."""

    code: str  # anonymous tracking code assigned at first sighting, e.g. "H-03"
    unit_id: str
    last_x: int
    last_y: int
    last_seen_turn: int
    visible: bool = True
