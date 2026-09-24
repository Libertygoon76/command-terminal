"""Generate data/map/world.json from the region layout below.

WARNING: this OVERWRITES data/map/world.json. world.json is the source of truth the game
reads. Use this script only when you want to redraw the geography from scratch; for small
tweaks, edit world.json directly (keep every `base` row exactly `width` characters).

    python tools/generate_world_map.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

W, H = 128, 42
OUT = Path(__file__).resolve().parents[1] / "data" / "map" / "world.json"

# Rects are inclusive [x0, y0, x1, y1]. Later regions win where rects overlap.
REGIONS = [
    {"id": "port_cassel", "name": "Port Cassel", "owner": "kestria", "terrain": "coastal",
     "rects": [[4, 4, 24, 13]], "label": [8, 5]},
    {"id": "harrowfen", "name": "Harrowfen", "owner": "kestria", "terrain": "marsh",
     "rects": [[25, 4, 52, 17]], "label": [34, 6]},
    {"id": "aldmark", "name": "Aldmark", "owner": "kestria", "terrain": "urban", "capital": True,
     "rects": [[4, 14, 24, 31], [25, 18, 34, 31]], "label": [8, 16]},
    {"id": "greywater", "name": "Greywater", "owner": "kestria", "terrain": "river",
     "rects": [[35, 18, 66, 31]], "label": [38, 20]},
    {"id": "stonereach", "name": "Stonereach", "owner": "kestria", "terrain": "mountain",
     "rects": [[53, 4, 66, 17]], "label": [55, 6]},
    {"id": "frontier", "name": "The Frontier", "owner": "contested", "terrain": "plains",
     "rects": [[67, 4, 81, 31]], "label": [69, 29]},
    {"id": "karzan", "name": "Karzan Oblast", "owner": "vosk", "terrain": "urban", "capital": True,
     "rects": [[82, 4, 110, 17]], "label": [84, 6]},
    {"id": "vosk_plains", "name": "Vosk Plains", "owner": "vosk", "terrain": "plains",
     "rects": [[82, 18, 110, 31]], "label": [99, 29]},
    {"id": "tal_varos", "name": "Tal Varos", "owner": "vosk", "terrain": "mountain",
     "rects": [[111, 4, 123, 31]], "label": [113, 6]},
    {"id": "iren_free_port", "name": "Iren Free Port", "owner": "neutral", "terrain": "urban",
     "rects": [[57, 35, 74, 39]], "label": [59, 36]},
]

TERRAIN_TYPES = {
    "plains":   {"glyph": ".", "density": 0.05, "movement": 1.0, "defense": 1.0,
                 "description": "Open ground. Good for armor, deadly for infantry in the open."},
    "coastal":  {"glyph": ",", "density": 0.05, "movement": 1.0, "defense": 1.05,
                 "description": "Coastal lowland and harbours."},
    "marsh":    {"glyph": "≈", "density": 0.16, "movement": 0.5, "defense": 1.15,
                 "description": "Fen and bog. Vehicles bog down; supply is slow."},
    "urban":    {"glyph": "#", "density": 0.05, "movement": 0.7, "defense": 1.4,
                 "description": "Built-up area. Strong defensive terrain; costly to assault."},
    "river":    {"glyph": ".", "density": 0.05, "movement": 0.8, "defense": 1.2,
                 "description": "River valley. Crossings are chokepoints."},
    "mountain": {"glyph": "^", "density": 0.30, "movement": 0.4, "defense": 1.5,
                 "description": "High ground. Very slow movement, excellent defense."},
    "sea":      {"glyph": "~", "density": 0.10, "movement": 0.0, "defense": 1.0,
                 "description": "Open water."},
}

FEATURES = [
    {"type": "capital", "name": "Aldmark", "x": 14, "y": 22},
    {"type": "port", "name": "Port Cassel", "x": 12, "y": 9},
    {"type": "city", "name": "Greywater", "x": 45, "y": 26},
    {"type": "city", "name": "Harrow", "x": 40, "y": 11},
    {"type": "capital", "name": "Karzan", "x": 96, "y": 10},
    {"type": "city", "name": "Novo-Vosk", "x": 96, "y": 25},
    {"type": "port", "name": "Iren", "x": 66, "y": 37},
]
FEATURE_GLYPH = {"capital": "★", "city": "◉", "port": "⊕"}

BORDER_GLYPHS = {
    # (up, down, left, right) -> glyph
    (False, False, True, True): "─", (True, True, False, False): "│",
    (False, True, False, True): "┌", (False, True, True, False): "┐",
    (True, False, False, True): "└", (True, False, True, False): "┘",
    (True, True, False, True): "├", (True, True, True, False): "┤",
    (False, True, True, True): "┬", (True, False, True, True): "┴",
    (True, True, True, True): "┼",
    (False, False, False, True): "─", (False, False, True, False): "─",
    (True, False, False, False): "│", (False, True, False, False): "│",
    (False, False, False, False): "┼",
}


def main() -> None:
    rng = random.Random(1984)
    region_grid = [[None] * W for _ in range(H)]
    for region in REGIONS:
        for x0, y0, x1, y1 in region["rects"]:
            for y in range(y0, y1 + 1):
                for x in range(x0, x1 + 1):
                    region_grid[y][x] = region["id"]
    by_id = {r["id"]: r for r in REGIONS}

    def rid(x, y):
        return region_grid[y][x] if 0 <= x < W and 0 <= y < H else None

    # Borders: first column/row of a region adjacent to a different region (or sea), plus coast edges.
    border = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            r = rid(x, y)
            if r is None:
                continue
            if rid(x - 1, y) != r or rid(x, y - 1) != r or rid(x + 1, y) is None or rid(x, y + 1) is None:
                border[y][x] = True

    grid = [[" "] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            r = rid(x, y)
            if r is None:
                if rng.random() < TERRAIN_TYPES["sea"]["density"]:
                    grid[y][x] = "~"
                continue
            if border[y][x]:
                def b(xx, yy):
                    return 0 <= xx < W and 0 <= yy < H and border[yy][xx]
                grid[y][x] = BORDER_GLYPHS[(b(x, y - 1), b(x, y + 1), b(x - 1, y), b(x + 1, y))]
            else:
                terrain = TERRAIN_TYPES[by_id[r]["terrain"]]
                if rng.random() < terrain["density"]:
                    grid[y][x] = terrain["glyph"]

    def put(x, y, text, only_blank=False):
        for i, ch in enumerate(text):
            if 0 <= x + i < W and 0 <= y < H:
                if only_blank and grid[y][x + i] not in " .,~#^≈":
                    continue
                grid[y][x + i] = ch

    def clear(x0, y0, x1, y1):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if not border[y][x]:
                    grid[y][x] = " "

    # Greywater river: meanders from Stonereach down to the southern sea.
    x = 58
    for y in range(8, 34):
        x += rng.choice((-1, 0, 0, 1))
        x = max(47, min(62, x))
        if rid(x, y) is None or not border[y][x]:
            grid[y][x] = "≈"

    # Rail lines.
    for x in range(15, 71):
        if not border[24][x]:
            grid[24][x] = "═"
    for y in range(11, 25):
        if not border[y][96]:
            grid[y][96] = "║"
    for x in range(78, 97):
        if not border[24][x]:
            grid[24][x] = "═"
    grid[24][96] = "╝"

    # Frontier: Kestrian trench line, no-man's-land, Vosk trench line.
    clear(68, 5, 80, 30)
    for y in range(5, 31):
        grid[y][71] = "┆"
        grid[y][76] = "┆"
        for x in (72, 73, 74, 75):
            if rng.random() < 0.12:
                grid[y][x] = rng.choice("°·'")
    for x in range(68, 81):
        if grid[24][x] in " °·'":
            grid[24][x] = "═" if x < 71 or x > 76 else "╌"

    # Cities, labels, sea titles, compass, scale bar.
    region_names = {r["name"].upper() for r in REGIONS}
    for f in FEATURES:
        grid[f["y"]][f["x"]] = FEATURE_GLYPH[f["type"]]
        if f["name"].upper() not in region_names:  # don't repeat the province label
            put(f["x"] + 1, f["y"], f" {f['name'].upper()} ")
    for region in REGIONS:
        lx, ly = region["label"]
        put(lx - 1, ly, f" {region['name'].upper()} ")
    put(34, 1, "N  O  R  T  H  E  R  N     S  E  A")
    put(18, 34, "I  R  E  N     S  T  R  A  I  T  S")
    put(84, 36, "I  R  E  N     S  T  R  A  I  T  S")
    put(4, 35, "    N    ")
    put(4, 36, "  W─┼─E  ")
    put(4, 37, "    S    ")
    put(96, 39, "├────┼────┤ 50 KM")
    put(2, 40, "GRID REF: COLUMN-ROW · CLASSIFIED // KESTRIAN GENERAL STAFF · SHEET 1 OF 1")

    base = ["".join(row) for row in grid]
    assert all(len(row) == W for row in base)

    data = {
        "_comment": [
            "War Room map. `base` is the rendered background: exactly `height` rows of `width` characters.",
            "Coordinates are terminal cells: x = column (0 = left), y = row (0 = top).",
            "Region rects are inclusive [x0, y0, x1, y1]; later regions win on overlap. Cells outside all regions are sea.",
            "Generated by tools/generate_world_map.py (which overwrites this file).",
        ],
        "width": W,
        "height": H,
        "terrain_types": {k: {kk: vv for kk, vv in v.items() if kk != "density"} for k, v in TERRAIN_TYPES.items()},
        "regions": [{k: v for k, v in r.items()} for r in REGIONS],
        "features": FEATURES,
        "base": base,
    }
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n".join(base))


if __name__ == "__main__":
    main()
