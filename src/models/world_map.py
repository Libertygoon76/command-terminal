from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Rect = tuple[int, int, int, int]  # inclusive x0, y0, x1, y1


@dataclass(frozen=True)
class MapRegion:
    id: str
    name: str
    owner: str  # nation id, "neutral" or "contested"
    terrain: str
    rects: tuple[Rect, ...]  # coarse outline (the world's region grid is authoritative)
    label: tuple[int, int]
    capital: bool = False
    key: str = ""  # one-letter code in world.json region_rows

    def contains(self, x: int, y: int) -> bool:
        """Coarse test against the outline rects. Prefer WorldMap.in_region for exact membership."""
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in self.rects)


@dataclass(frozen=True)
class MapFeature:
    type: str  # capital | city | industrial | port | town
    name: str
    x: int
    y: int
    harbour: tuple[int, int] | None = None  # ports: the sea cell where warships dock
    trade: int = 0  # ports: overseas trade (CR/week), lost while blockaded

    @property
    def location(self) -> tuple[int, int]:
        return self.x, self.y


@dataclass(frozen=True)
class SeaZone:
    name: str
    rect: Rect


@dataclass
class WorldMap:
    """The War Room map: a fixed character grid with regions laid over it by exact X/Y cells.

    Scale: one row = 10 miles; a column is half a row (5 miles), see config map.column_scale.
    """

    width: int
    height: int
    base: list[str]  # rendered background, `height` rows of exactly `width` characters
    regions: dict[str, MapRegion]
    features: list[MapFeature] = field(default_factory=list)
    terrain_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Transport/obstacle layer: cell -> "rail" | "road" | "destroyed_rail" | "trench" (rail wins over road).
    transport: dict[tuple[int, int], str] = field(default_factory=dict)
    region_rows: list[str] | None = None  # exact region key per cell ('~' = sea); else built from rects
    sea_zones: list[SeaZone] = field(default_factory=list)
    _cells: list[list[str | None]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if len(self.base) != self.height or any(len(row) != self.width for row in self.base):
            raise ValueError(f"map base must be {self.height} rows of exactly {self.width} characters")
        self._cells = [[None] * self.width for _ in range(self.height)]
        if self.region_rows is not None:
            if len(self.region_rows) != self.height or any(len(r) != self.width for r in self.region_rows):
                raise ValueError(f"region_rows must be {self.height} rows of exactly {self.width} characters")
            by_key = {r.key: r.id for r in self.regions.values() if r.key}
            for y, row in enumerate(self.region_rows):
                for x, key in enumerate(row):
                    if key == "~":
                        continue
                    if key not in by_key:
                        raise ValueError(f"region_rows: unknown region key {key!r} at {x},{y}")
                    self._cells[y][x] = by_key[key]
            return
        # Legacy maps: later regions win on overlap.
        for region in self.regions.values():
            for x0, y0, x1, y1 in region.rects:
                if not (0 <= x0 <= x1 < self.width and 0 <= y0 <= y1 < self.height):
                    raise ValueError(f"region {region.id!r}: rect {(x0, y0, x1, y1)} is outside the map")
                for y in range(y0, y1 + 1):
                    for x in range(x0, x1 + 1):
                        self._cells[y][x] = region.id

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def region_at(self, x: int, y: int) -> MapRegion | None:
        if not self.in_bounds(x, y):
            return None
        region_id = self._cells[y][x]
        return self.regions[region_id] if region_id else None

    def in_region(self, region_id: str, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and self._cells[y][x] == region_id

    def region_cells(self, region_id: str) -> list[tuple[int, int]]:
        return [(x, y) for y in range(self.height) for x in range(self.width) if self._cells[y][x] == region_id]

    def owner_at(self, x: int, y: int) -> str | None:
        region = self.region_at(x, y)
        return region.owner if region else None

    def is_sea(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and self._cells[y][x] is None

    def is_coastal(self, x: int, y: int) -> bool:
        """A land cell touching the sea (4-neighbourhood)."""
        return not self.is_sea(x, y) and any(self.is_sea(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))

    def is_national_border(self, x: int, y: int) -> bool:
        """True if this cell touches a region with a different owner."""
        owner = self.owner_at(x, y)
        if owner is None:
            return False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            other = self.owner_at(x + dx, y + dy)
            if other is not None and other != owner:
                return True
        return False

    def transport_at(self, x: int, y: int) -> str | None:
        return self.transport.get((x, y))

    def terrain_info(self, terrain: str) -> dict[str, Any]:
        return self.terrain_types.get(terrain, {})

    def sea_zone_at(self, x: int, y: int) -> str:
        for zone in self.sea_zones:
            x0, y0, x1, y1 = zone.rect
            if x0 <= x <= x1 and y0 <= y <= y1:
                return zone.name
        return "the Open Sea"

    def ports(self) -> list[MapFeature]:
        return [f for f in self.features if f.type == "port"]

    def feature_named(self, name: str) -> MapFeature | None:
        return next((f for f in self.features if f.name == name), None)

    def place_name(self, x: int, y: int) -> str:
        """Region name on land, sea-zone name at sea."""
        region = self.region_at(x, y)
        return region.name if region else self.sea_zone_at(x, y)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldMap:
        regions = {}
        for raw in data["regions"]:
            regions[raw["id"]] = MapRegion(
                id=raw["id"],
                name=raw["name"],
                owner=raw["owner"],
                terrain=raw["terrain"],
                rects=tuple(tuple(r) for r in raw["rects"]),
                label=tuple(raw.get("label", raw["rects"][0][:2])),
                capital=bool(raw.get("capital", False)),
                key=raw.get("key", ""),
            )
        transport: dict[tuple[int, int], str] = {}
        precedence = ("trench", "road", "destroyed_rail", "rail")  # later kinds override earlier ones
        for kind in precedence:
            for x, y in data.get("transport", {}).get(kind, []):
                transport[(int(x), int(y))] = kind
        features = [
            MapFeature(f["type"], f["name"], int(f["x"]), int(f["y"]),
                       harbour=tuple(f["harbour"]) if f.get("harbour") else None, trade=int(f.get("trade", 0)))
            for f in data.get("features", [])
        ]
        return cls(
            width=int(data["width"]),
            height=int(data["height"]),
            base=list(data["base"]),
            regions=regions,
            features=features,
            terrain_types=dict(data.get("terrain_types", {})),
            transport=transport,
            region_rows=list(data["region_rows"]) if data.get("region_rows") else None,
            sea_zones=[SeaZone(z["name"], tuple(z["rect"])) for z in data.get("sea_zones", [])],
        )
