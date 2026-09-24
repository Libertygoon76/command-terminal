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
    rects: tuple[Rect, ...]
    label: tuple[int, int]
    capital: bool = False

    def contains(self, x: int, y: int) -> bool:
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in self.rects)


@dataclass(frozen=True)
class MapFeature:
    type: str  # capital | city | port
    name: str
    x: int
    y: int


@dataclass
class WorldMap:
    """The War Room map: a fixed character grid with regions laid over it by exact X/Y cells."""

    width: int
    height: int
    base: list[str]  # rendered background, `height` rows of exactly `width` characters
    regions: dict[str, MapRegion]
    features: list[MapFeature] = field(default_factory=list)
    terrain_types: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Transport/obstacle layer: cell -> "rail" | "road" | "destroyed_rail" | "trench" (rail wins over road).
    transport: dict[tuple[int, int], str] = field(default_factory=dict)
    _cells: list[list[str | None]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if len(self.base) != self.height or any(len(row) != self.width for row in self.base):
            raise ValueError(f"map base must be {self.height} rows of exactly {self.width} characters")
        # Precompute cell -> region id; later regions win on overlap.
        self._cells = [[None] * self.width for _ in range(self.height)]
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

    def owner_at(self, x: int, y: int) -> str | None:
        region = self.region_at(x, y)
        return region.owner if region else None

    def is_sea(self, x: int, y: int) -> bool:
        return self.region_at(x, y) is None

    def is_national_border(self, x: int, y: int) -> bool:
        """True if this cell touches a region with a different owner (or the sea edge of the land)."""
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
            )
        transport: dict[tuple[int, int], str] = {}
        precedence = ("trench", "road", "destroyed_rail", "rail")  # later kinds override earlier ones
        for kind in precedence:
            for x, y in data.get("transport", {}).get(kind, []):
                transport[(int(x), int(y))] = kind
        return cls(
            width=int(data["width"]),
            height=int(data["height"]),
            base=list(data["base"]),
            regions=regions,
            features=[MapFeature(f["type"], f["name"], int(f["x"]), int(f["y"])) for f in data.get("features", [])],
            terrain_types=dict(data.get("terrain_types", {})),
            transport=transport,
        )
