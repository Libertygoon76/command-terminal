"""Generate data/map/world.json: the continental War Room map.

WARNING: this OVERWRITES data/map/world.json (and prints the result). world.json is the source of
truth the game reads; re-run this only to redraw the geography from scratch.

    python tools/generate_world_map.py

Scale: one grid row = 10 miles. Terminal characters are twice as tall as they are wide, so a
column is half a row (5 miles) — config map.column_scale = 0.5 keeps distances true.

Construction:
  1. A coarse BLUEPRINT (48 x 20 blocks, each 5 columns x 4 rows) assigns every block to a region
     or to the sea.
  2. Coastlines are roughened with value noise (never across the Frontier, whose trench geometry
     must stay exact); stray islets and lakes are removed.
  3. Settlements are snapped onto their region (ports onto the coast, with a `harbour` sea cell
     where warships dock and muster), then railways and roads are routed over land with A*.
  4. Everything is drawn into `base`; the exact per-cell region map goes into `region_rows`.
"""

from __future__ import annotations

import heapq
import json
import math
import random
from pathlib import Path

W, H = 240, 80
BX, BY = 5, 4  # blueprint block size (columns, rows)
OUT = Path(__file__).resolve().parents[1] / "data" / "map" / "world.json"
SEA = "~"

# Blueprint: per block-row, runs of (region key, first block column, last block column). Unlisted = sea.
BLUEPRINT = [
    [],
    [("P", 4, 5)],
    [("P", 3, 6), ("H", 7, 8), ("Z", 35, 39)],
    [("P", 3, 6), ("H", 7, 11), ("S", 12, 15), ("Z", 31, 41), ("T", 42, 44)],
    [("P", 4, 6), ("H", 7, 11), ("S", 12, 16), ("M", 17, 23), ("F", 24, 26), ("V", 27, 28), ("Z", 30, 39), ("T", 40, 46)],
    [("A", 5, 7), ("H", 8, 11), ("S", 12, 16), ("M", 17, 23), ("F", 24, 26), ("V", 27, 30), ("K", 31, 37), ("Z", 38, 39), ("T", 40, 47)],
    [("A", 4, 11), ("S", 12, 16), ("I", 17, 18), ("M", 19, 23), ("F", 24, 26), ("V", 27, 30), ("K", 31, 39), ("T", 40, 47)],
    [("A", 3, 13), ("I", 14, 18), ("M", 19, 23), ("F", 24, 26), ("V", 27, 30), ("K", 31, 39), ("T", 40, 47)],
    [("A", 3, 13), ("I", 14, 18), ("M", 19, 23), ("F", 24, 26), ("V", 27, 31), ("K", 32, 37), ("D", 38, 39), ("T", 40, 46), ("E", 47, 47)],
    [("W", 3, 4), ("A", 5, 13), ("I", 14, 18), ("M", 19, 23), ("F", 24, 26), ("V", 27, 31), ("D", 32, 40), ("T", 41, 44), ("E", 45, 47)],
    [("W", 3, 6), ("A", 7, 12), ("G", 13, 14), ("I", 15, 18), ("M", 19, 23), ("F", 24, 26), ("V", 27, 31), ("D", 32, 41), ("E", 42, 47)],
    [("W", 3, 7), ("G", 8, 18), ("M", 19, 23), ("F", 24, 26), ("V", 27, 31), ("D", 32, 40), ("E", 41, 47)],
    [("W", 4, 7), ("G", 8, 19), ("M", 20, 23), ("F", 24, 26), ("V", 27, 32), ("R", 33, 37), ("D", 38, 40), ("E", 41, 47)],
    [("W", 5, 8), ("G", 9, 20), ("M", 21, 23), ("F", 24, 26), ("V", 27, 30), ("R", 31, 40), ("E", 41, 47)],
    [("W", 6, 8), ("G", 9, 18), ("R", 29, 40), ("E", 41, 47)],
    [("G", 11, 15), ("R", 31, 37), ("E", 41, 47)],
    [("E", 43, 47)],
    [("N", 23, 26)],
    [("N", 23, 26)],
    [],
]

REGIONS = {
    "P": {"id": "port_cassel", "name": "Cassel Coast", "owner": "kestria", "terrain": "coastal"},
    "H": {"id": "harrowfen", "name": "Harrowfen", "owner": "kestria", "terrain": "marsh"},
    "A": {"id": "aldmark", "name": "Aldmark", "owner": "kestria", "terrain": "plains", "capital": True},
    "W": {"id": "westmarch", "name": "Westmarch", "owner": "kestria", "terrain": "coastal"},
    "G": {"id": "greywater", "name": "Greywater", "owner": "kestria", "terrain": "river"},
    "S": {"id": "stonereach", "name": "Stonereach", "owner": "kestria", "terrain": "mountain"},
    "I": {"id": "ironvale", "name": "Ironvale", "owner": "kestria", "terrain": "hills"},
    "M": {"id": "eastmarch", "name": "Eastmarch", "owner": "kestria", "terrain": "plains"},
    "F": {"id": "frontier", "name": "The Frontier", "owner": "contested", "terrain": "plains"},
    "V": {"id": "vosk_marches", "name": "Vosk Marches", "owner": "vosk", "terrain": "plains"},
    "K": {"id": "karzan", "name": "Karzan Oblast", "owner": "vosk", "terrain": "plains", "capital": True},
    "Z": {"id": "zarnov", "name": "Zarnov Coast", "owner": "vosk", "terrain": "coastal"},
    "D": {"id": "dornsk", "name": "Dornsk Basin", "owner": "vosk", "terrain": "hills"},
    "R": {"id": "sevrask", "name": "Sevrask Coast", "owner": "vosk", "terrain": "coastal"},
    "T": {"id": "tal_varos", "name": "Tal Varos", "owner": "vosk", "terrain": "mountain"},
    "E": {"id": "eastern_steppe", "name": "Eastern Steppe", "owner": "vosk", "terrain": "plains"},
    "N": {"id": "iren_free_port", "name": "Iren Free Port", "owner": "neutral", "terrain": "urban"},
}

TERRAIN_TYPES = {
    "plains":   {"glyph": ".", "density": 0.04, "movement": 1.0, "defense": 1.0,
                 "description": "Open farmland and steppe. Good for armor, deadly for infantry in the open."},
    "coastal":  {"glyph": ",", "density": 0.05, "movement": 1.0, "defense": 1.05,
                 "description": "Coastal lowland, dunes and harbours. Within reach of naval gunfire."},
    "marsh":    {"glyph": "≈", "density": 0.14, "movement": 0.5, "defense": 1.15,
                 "description": "Fen and bog. Vehicles bog down; supply is slow."},
    "urban":    {"glyph": "#", "density": 0.05, "movement": 0.7, "defense": 1.4,
                 "description": "Built-up area. Strong defensive terrain; costly to assault."},
    "river":    {"glyph": ".", "density": 0.04, "movement": 0.8, "defense": 1.2,
                 "description": "River valley. Crossings are chokepoints."},
    "hills":    {"glyph": "∩", "density": 0.12, "movement": 0.7, "defense": 1.25,
                 "description": "Industrial uplands: pit-heads, slag heaps and railway cuttings. Good defensive ground."},
    "mountain": {"glyph": "^", "density": 0.28, "movement": 0.4, "defense": 1.5,
                 "description": "High ground. Very slow movement, excellent defense."},
    "sea":      {"glyph": "~", "density": 0.22, "movement": 0.0, "defense": 1.0,
                 "description": "Open water. Only warships move here."},
}

# Settlements: (type, name, region key, approximate (x, y)[, weekly overseas trade for ports]).
FEATURES = [
    ("capital", "Aldmark", "A", (45, 30)),
    ("port", "Port Cassel", "P", (24, 9), 5000),
    ("port", "Northwatch", "H", (44, 9), 1000),
    ("port", "Saltmere", "W", (16, 44), 3000),
    ("port", "Greyhaven", "G", (66, 63), 3000),
    ("port", "Brenmouth", "M", (100, 16), 1000),
    ("city", "Harrow", "H", (48, 18)),
    ("city", "Greywater", "G", (70, 50)),
    ("industrial", "Ironvale", "I", (84, 30)),
    ("industrial", "Kessel Works", "I", (76, 40)),
    ("town", "Wyck", "P", (22, 18)),
    ("town", "Fenwick", "H", (35, 14)),
    ("town", "Stanhope", "S", (66, 18)),
    ("town", "Cairn", "S", (80, 21)),
    ("town", "Marrow", "A", (30, 24)),
    ("town", "Tollbridge", "A", (58, 26)),
    ("town", "Ashby", "A", (40, 38)),
    ("town", "Colden", "W", (26, 40)),
    ("town", "Wexham", "W", (32, 50)),
    ("town", "Briar", "G", (52, 46)),
    ("town", "Lowmoor", "G", (56, 56)),
    ("town", "Rook's Ford", "G", (84, 53)),
    ("town", "Dunhollow", "G", (93, 45)),
    ("town", "Halden", "M", (108, 24)),
    ("town", "Kestrel Cross", "M", (104, 36)),
    ("town", "Morrow Field", "M", (110, 48)),
    ("capital", "Karzan", "K", (175, 26)),
    ("port", "Zarnovsk", "Z", (185, 9), 6000),
    ("port", "Kolvaan", "Z", (153, 15), 2000),
    ("port", "Sevrask", "R", (185, 62), 6000),
    ("port", "Kalinsk", "R", (150, 58), 2000),
    ("industrial", "Dornsk", "D", (190, 40)),
    ("industrial", "Novo-Vosk", "D", (165, 42)),
    ("city", "Tal Varos", "T", (215, 22)),
    ("city", "Yevrask", "E", (222, 50)),
    ("town", "Grodna", "V", (140, 21)),
    ("town", "Pskel", "V", (146, 30)),
    ("town", "Bereza", "V", (140, 41)),
    ("town", "Kurav", "V", (146, 50)),
    ("town", "Vetka", "K", (162, 24)),
    ("town", "Rovno", "K", (190, 26)),
    ("town", "Molodva", "K", (178, 33)),
    ("town", "Istrov", "Z", (200, 13)),
    ("town", "Zhelan", "D", (180, 45)),
    ("town", "Selvin", "R", (168, 54)),
    ("town", "Dunai", "R", (198, 57)),
    ("town", "Khorsk", "E", (214, 40)),
    ("town", "Barsuk", "E", (226, 60)),
    ("town", "Ulyan", "T", (230, 28)),
    ("port", "Iren", "N", (125, 71), 0),
]
FEATURE_GLYPH = {"capital": "★", "city": "◉", "industrial": "▣", "port": "⊕", "town": "○"}

# The Frontier: exact trench geometry (never roughened).
FRONT_X0, FRONT_X1 = 120, 134
FRONT_Y0, FRONT_Y1 = 16, 55
KES_TRENCH, VOSK_TRENCH = 123, 128
RAIL_CROSSING_Y = 36
ROAD_CROSSINGS_Y = (20, 29, 47)
PROTECT_X = (114, 140)  # no coastline noise across the front

# Transport, by settlement name or (x, y).
RAILS = [
    ("Port Cassel", "Aldmark"), ("Saltmere", "Aldmark"), ("Northwatch", "Harrow"), ("Harrow", "Aldmark"),
    ("Aldmark", "Kessel Works"), ("Kessel Works", "Ironvale"), ("Ironvale", "Halden"), ("Halden", "Brenmouth"),
    ("Ironvale", "Kestrel Cross"), ("Kestrel Cross", (122, RAIL_CROSSING_Y)), ("Aldmark", "Greywater"),
    ("Greywater", "Greyhaven"), ("Greywater", "Rook's Ford"), ("Rook's Ford", "Morrow Field"),
    ("Halden", "Kestrel Cross"), ("Kestrel Cross", "Morrow Field"),
    ("Karzan", "Zarnovsk"), ("Karzan", "Vetka"), ("Vetka", "Kolvaan"), ("Karzan", "Pskel"), ("Pskel", "Bereza"),
    ("Bereza", (129, RAIL_CROSSING_Y)), ("Grodna", "Pskel"), ("Bereza", "Kurav"), ("Karzan", "Molodva"),
    ("Molodva", "Dornsk"), ("Dornsk", "Sevrask"), ("Novo-Vosk", "Bereza"), ("Novo-Vosk", "Kalinsk"),
    ("Novo-Vosk", "Molodva"), ("Karzan", "Rovno"), ("Rovno", "Tal Varos"), ("Tal Varos", "Ulyan"),
    ("Ulyan", (238, 28)), ("Dornsk", "Khorsk"), ("Khorsk", "Yevrask"), ("Yevrask", (238, 50)),
]
EXTRA_ROADS = [
    ("Halden", (122, ROAD_CROSSINGS_Y[0])), ("Kestrel Cross", (122, ROAD_CROSSINGS_Y[1])),
    ("Morrow Field", (122, ROAD_CROSSINGS_Y[2])), ("Grodna", (129, ROAD_CROSSINGS_Y[0])),
    ("Pskel", (129, ROAD_CROSSINGS_Y[1])), ("Kurav", (129, ROAD_CROSSINGS_Y[2])),
    ("Brenmouth", "Halden"), ("Cairn", "Ironvale"), ("Stanhope", "Harrow"), ("Wyck", "Port Cassel"),
    ("Tollbridge", "Aldmark"), ("Dunhollow", "Kestrel Cross"), ("Istrov", "Zarnovsk"), ("Barsuk", "Yevrask"),
]
RIVERS = [[(72, 20), (66, 34), (70, 48), (64, 62)], [(182, 30), (176, 44), (182, 60)]]

SEA_ZONES = [  # first match wins; the bays either side of the Frontier belong to the seas beyond
    {"name": "Western Ocean", "rect": [0, 16, 20, 63]},
    {"name": "Northern Sea", "rect": [0, 0, W - 1, 24]},
    {"name": "Iren Straits", "rect": [0, 46, 150, H - 1]},
    {"name": "Gulf of Sevrask", "rect": [151, 46, W - 1, H - 1]},
]
SEA_LABELS = [(" N  O  R  T  H  E  R  N     S  E  A ", (64, 2)), (" N  O  R  T  H  E  R  N     S  E  A ", (178, 2)),
              (" WESTERN ", (2, 30)), (" OCEAN ", (3, 31)),
              (" I  R  E  N     S  T  R  A  I  T  S ", (30, 72)),
              (" G U L F   O F   S E V R A S K ", (160, 73))]

BORDER_GLYPHS = {
    (False, False, True, True): "─", (True, True, False, False): "│",
    (False, True, False, True): "┌", (False, True, True, False): "┐",
    (True, False, False, True): "└", (True, False, True, False): "┘",
    (True, True, False, True): "├", (True, True, True, False): "┤",
    (False, True, True, True): "┬", (True, False, True, True): "┴",
    (True, True, True, True): "┼",
    (False, False, False, True): "─", (False, False, True, False): "─",
    (True, False, False, False): "│", (False, True, False, False): "│",
    (False, False, False, False): "·",
}
STEPS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


def value_noise(rng: random.Random, spacing: int) -> list[list[float]]:
    gw, gh = W // spacing + 2, H // spacing + 2
    lattice = [[rng.uniform(-1, 1) for _ in range(gw)] for _ in range(gh)]
    field = []
    for y in range(H):
        row = []
        for x in range(W):
            gx, gy = x / spacing, y / spacing
            x0, y0 = int(gx), int(gy)
            tx, ty = gx - x0, gy - y0
            tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
            a = lattice[y0][x0] * (1 - tx) + lattice[y0][x0 + 1] * tx
            b = lattice[y0 + 1][x0] * (1 - tx) + lattice[y0 + 1][x0 + 1] * tx
            row.append(a * (1 - ty) + b * ty)
        field.append(row)
    return field


def main() -> None:
    rng = random.Random(1984)

    # --- 1. blueprint -> region grid ----------------------------------------------------------
    blue: list[list[str]] = [[SEA] * W for _ in range(H)]
    for by, runs in enumerate(BLUEPRINT):
        for key, b0, b1 in runs:
            for y in range(by * BY, by * BY + BY):
                for x in range(b0 * BX, b1 * BX + BX):
                    blue[y][x] = key
    grid = [row[:] for row in blue]

    # --- 2. roughen coastlines ------------------------------------------------------------------
    n1, n2 = value_noise(rng, 7), value_noise(rng, 3)
    r = 3
    for y in range(H):
        for x in range(W):
            if PROTECT_X[0] <= x <= PROTECT_X[1] and y <= 60:
                continue
            window = [blue[yy][xx] for yy in range(max(0, y - r), min(H, y + r + 1))
                      for xx in range(max(0, x - r), min(W, x + r + 1))]
            land = sum(1 for c in window if c != SEA) / len(window)
            if land in (0.0, 1.0):
                continue
            noisy = land + 0.55 * n1[y][x] + 0.25 * n2[y][x]
            if noisy > 0.5 and grid[y][x] == SEA:
                near = [c for c in window if c not in (SEA, "F")]
                if near:
                    grid[y][x] = max(sorted(set(near)), key=near.count)
            elif noisy <= 0.5 and grid[y][x] not in (SEA, "F"):
                grid[y][x] = SEA
    for y in range(H):  # no land on the map frame except the Vosk hinterland (east edge)
        for x in range(W):
            if (x < 2 or y < 1 or y > H - 2) and grid[y][x] != SEA:
                grid[y][x] = SEA

    def components(is_member):
        seen, comps = set(), []
        for y in range(H):
            for x in range(W):
                if (x, y) in seen or not is_member(x, y):
                    continue
                comp, stack = [], [(x, y)]
                seen.add((x, y))
                while stack:
                    cx, cy = stack.pop()
                    comp.append((cx, cy))
                    for dx, dy in STEPS4:
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen and is_member(nx, ny):
                            seen.add((nx, ny))
                            stack.append((nx, ny))
                comps.append(comp)
        return comps

    for comp in components(lambda x, y: grid[y][x] != SEA):  # drop islets
        if len(comp) < 60:
            for x, y in comp:
                grid[y][x] = SEA
    sea_comps = components(lambda x, y: grid[y][x] == SEA)
    ocean = set(max(sea_comps, key=len))
    for comp in sea_comps:  # fill lakes with the surrounding region
        if comp[0] in ocean:
            continue
        for x, y in comp:
            grid[y][x] = blue[y][x] if blue[y][x] != SEA else next(
                (grid[y + dy][x + dx] for dx, dy in STEPS4 if grid[y + dy][x + dx] != SEA), "E")

    def rid(x, y):
        return grid[y][x] if 0 <= x < W and 0 <= y < H and grid[y][x] != SEA else None

    def is_sea(x, y):
        return 0 <= x < W and 0 <= y < H and grid[y][x] == SEA

    def coastal(x, y):
        return rid(x, y) is not None and any(is_sea(x + dx, y + dy) for dx, dy in STEPS4)

    # --- 3. borders between land regions (national borders are coloured by the renderer) ---------
    border = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            k = rid(x, y)
            if k is not None and (rid(x - 1, y) not in (k, None) or rid(x, y - 1) not in (k, None)):
                border[y][x] = True

    # --- 4. base terrain glyphs ------------------------------------------------------------------
    base = [[" "] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            k = rid(x, y)
            if k is None:
                if rng.random() < TERRAIN_TYPES["sea"]["density"]:
                    base[y][x] = "~"
                continue
            if border[y][x]:
                def b(xx, yy):
                    return 0 <= xx < W and 0 <= yy < H and border[yy][xx]
                base[y][x] = BORDER_GLYPHS[(b(x, y - 1), b(x, y + 1), b(x - 1, y), b(x + 1, y))]
            else:
                terrain = TERRAIN_TYPES[REGIONS[k]["terrain"]]
                if rng.random() < terrain["density"]:
                    base[y][x] = terrain["glyph"]

    for points in RIVERS:  # decorative: the valley's `river` terrain carries the effect
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            x = x0
            for y in range(y0, y1 + 1):
                target = x0 + (x1 - x0) * (y - y0) / max(1, y1 - y0)
                if rng.random() < 0.7:
                    x += 1 if target > x else -1 if target < x else 0
                else:
                    x += rng.choice((-1, 0, 1))
                for xx in (x, x + 1):
                    if rid(xx, y) is not None and not border[y][xx]:
                        base[y][xx] = "≈"

    # --- 5. settlements ---------------------------------------------------------------------------
    features, placed = [], {}
    taken: set[tuple[int, int]] = set()
    for spec in FEATURES:
        ftype, name, key, (tx, ty) = spec[:4]
        best = None
        for y in range(H):
            for x in range(2, W - 2):
                if rid(x, y) != key or border[y][x] or (x, y) in taken:
                    continue
                if ftype == "port" and not coastal(x, y):
                    continue
                if FRONT_X0 - 1 <= x <= FRONT_X1 + 1 and key != "N":
                    continue
                d = math.hypot((x - tx) * 0.5, y - ty)
                if best is None or d < best[0]:
                    best = (d, x, y)
        assert best, f"no place for {name}"
        _, x, y = best
        feature = {"type": ftype, "name": name, "x": x, "y": y}
        taken.update((x + dx, y + dy) for dx in range(-3, 4) for dy in (-1, 0, 1))
        if ftype == "port":
            feature["trade"] = spec[4]
            options = [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                       if is_sea(x + dx, y + dy) and 1 <= x + dx <= W - 2]
            hx, hy = max(options, key=lambda c: (sum(is_sea(c[0] + i, c[1] + j) for i in range(-2, 3)
                                                     for j in range(-2, 3)), -abs(c[0] - x)))
            feature["harbour"] = [hx, hy]
        features.append(feature)
        placed[name] = (x, y)

    # --- 6. railways and roads (A* over land, with a turn penalty for straight lines) --------------
    kind_at: dict[tuple[int, int], str] = {}

    def point(p):
        return placed[p] if isinstance(p, str) else tuple(p)

    def lay_route(a, b, kind):
        start, goal = point(a), point(b)

        def allowed(x, y):
            if rid(x, y) is None:
                return False
            if FRONT_X0 <= x <= FRONT_X1:
                rows = (RAIL_CROSSING_Y,) if kind == "rail" else ROAD_CROSSINGS_Y
                return y in rows and (x < KES_TRENCH or x > VOSK_TRENCH)
            return True

        def cost(x, y):
            c = {"mountain": 3.0, "marsh": 2.2, "hills": 1.4}.get(REGIONS[rid(x, y)]["terrain"], 1.0)
            if kind_at.get((x, y)) == kind or (kind == "road" and kind_at.get((x, y)) == "rail"):
                c = 0.35  # join an existing line
            return c

        def h(c):
            return (abs(c[0] - goal[0]) * 0.5 + abs(c[1] - goal[1])) * 0.35

        start_state = (start, None)
        best = {start_state: 0.0}
        came = {start_state: None}
        heap = [(h(start), 0.0, start, None)]
        end = None
        while heap:
            _, g, cell, d = heapq.heappop(heap)
            if g > best.get((cell, d), math.inf):
                continue
            if cell == goal:
                end = (cell, d)
                break
            for dx, dy in STEPS4:
                nxt = (cell[0] + dx, cell[1] + dy)
                if nxt != goal and not allowed(*nxt):
                    continue
                step = cost(*nxt) if nxt != goal else 1.0
                ng = g + step + (0.6 if d not in (None, (dx, dy)) else 0.0)
                state = (nxt, (dx, dy))
                if ng < best.get(state, math.inf):
                    best[state] = ng
                    came[state] = (cell, d)
                    heapq.heappush(heap, (ng + h(nxt), ng, nxt, (dx, dy)))
        assert end is not None, f"no {kind} route {a} -> {b}"
        state = end
        while state is not None:
            c = state[0]
            if not (kind == "road" and kind_at.get(c) == "rail"):
                kind_at[c] = kind
            state = came[state]

    for a, b in RAILS:
        lay_route(a, b, "rail")
    for a, b in EXTRA_ROADS:
        lay_route(a, b, "road")
    owner_of = {f["name"]: REGIONS[rid(f["x"], f["y"])]["owner"] for f in features}
    for f in features:  # every settlement off the network gets a road to its nearest neighbour
        if kind_at.get((f["x"], f["y"])) or f["name"] == "Iren":
            continue
        others = [g for g in features if g is not f and owner_of[g["name"]] == owner_of[f["name"]]]
        nearest = min(others, key=lambda g: abs(g["x"] - f["x"]) * 0.5 + abs(g["y"] - f["y"]))
        lay_route(f["name"], nearest["name"], "road")
    for x in range(KES_TRENCH, VOSK_TRENCH + 1):
        kind_at[(x, RAIL_CROSSING_Y)] = "destroyed_rail"
    for y in range(FRONT_Y0, FRONT_Y1 + 1):
        for x in (KES_TRENCH, VOSK_TRENCH):
            if kind_at.get((x, y)) != "destroyed_rail":
                kind_at[(x, y)] = "trench"
    transport: dict[str, list[list[int]]] = {"rail": [], "road": [], "destroyed_rail": [], "trench": []}
    for (x, y), kind in sorted(kind_at.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        transport[kind].append([x, y])

    for y in range(FRONT_Y0, FRONT_Y1 + 1):  # no-man's-land craters
        for x in range(KES_TRENCH + 1, VOSK_TRENCH):
            if rng.random() < 0.14 and not border[y][x]:
                base[y][x] = rng.choice("°'`")

    def same(xx, yy, kind):
        other = kind_at.get((xx, yy))
        return other == kind or (kind == "road" and other == "rail")

    def glyph_for(x, y, kind):
        if kind == "trench":
            return "┆"
        if kind == "destroyed_rail":
            return "╌"
        up, down, left, right = same(x, y - 1, kind), same(x, y + 1, kind), same(x - 1, y, kind), same(x + 1, y, kind)
        if kind == "rail":
            corner = {(True, False, True, False): "╝", (True, False, False, True): "╚",
                      (False, True, True, False): "╗", (False, True, False, True): "╔"}
            if (up or down) and (left or right):
                return corner.get((up, down, left, right), "╬")
            return "║" if (up or down) else "═"
        return "┊" if (up or down) and not (left or right) else "┈"

    for (x, y), kind in kind_at.items():
        if rid(x, y) is not None and not border[y][x]:
            base[y][x] = glyph_for(x, y, kind)

    # --- 7. settlement glyphs and labels ------------------------------------------------------------
    labelled: set[tuple[int, int]] = set()
    feature_cells = {(f["x"], f["y"]) for f in features}

    def free(x, y, allow_sea=False):
        if not (0 <= x < W and 0 <= y < H) or (x, y) in labelled or (x, y) in feature_cells:
            return False
        if rid(x, y) is None:
            return allow_sea
        return not border[y][x]

    def put(x, y, text, force=False, allow_sea=False):
        if not force and not all(free(x + i, y, allow_sea) for i in range(len(text))):
            return False
        for i, ch in enumerate(text):
            if 0 <= x + i < W:
                base[y][x + i] = ch
                labelled.add((x + i, y))
        return True

    for f in features:
        base[f["y"]][f["x"]] = FEATURE_GLYPH[f["type"]]
    order = ["capital", "port", "industrial", "city", "town"]
    for f in sorted(features, key=lambda f: order.index(f["type"])):
        text = f" {f['name'].upper() if f['type'] != 'town' else f['name']} "
        x, y = f["x"], f["y"]
        for lx, ly in ((x + 1, y), (x - len(text), y), (x - len(text) // 2, y + 1), (x - len(text) // 2, y - 1)):
            if put(lx, ly, text, allow_sea=f["type"] == "port"):
                break

    region_labels = {}
    for key, region in REGIONS.items():
        cells = [(x, y) for y in range(H) for x in range(W) if rid(x, y) == key]
        cx = sum(c[0] for c in cells) / len(cells)
        cy = sum(c[1] for c in cells) / len(cells)
        text = f" {region['name'].upper()} "
        spots = sorted(cells, key=lambda c: (math.hypot((c[0] + len(text) / 2 - cx) * 0.5, c[1] - cy), c))
        region_labels[key] = [cells[0][0], cells[0][1]]
        for x, y in spots:
            if all(rid(x + i, y) == key for i in range(len(text))) and put(x, y, text):
                region_labels[key] = [x + 1, y]
                break
    for text, (x, y) in SEA_LABELS:
        put(x, y, text, allow_sea=True)
    put(3, 66, "    N    ", allow_sea=True)
    put(3, 67, "  W─┼─E  ", allow_sea=True)
    put(3, 68, "    S    ", allow_sea=True)
    put(205, 76, "├────┼────┤ 50 MI", force=True)
    put(2, 78, "GRID REF: COLUMN-ROW · 1 ROW = 10 MI · 1 COLUMN = 5 MI · CLASSIFIED // KESTRIAN GENERAL "
               "STAFF · SHEET 1 OF 1", force=True)

    # --- 8. output ------------------------------------------------------------------------------
    rects: dict[str, list[list[int]]] = {k: [] for k in REGIONS}
    for by, runs in enumerate(BLUEPRINT):
        for key, b0, b1 in runs:
            rects[key].append([b0 * BX, by * BY, b1 * BX + BX - 1, by * BY + BY - 1])
    regions = []
    for key, region in REGIONS.items():
        entry = {"id": region["id"], "name": region["name"], "owner": region["owner"], "terrain": region["terrain"],
                 "key": key, "label": region_labels[key], "rects": rects[key]}
        if region.get("capital"):
            entry["capital"] = True
        regions.append(entry)

    rows = ["".join(row) for row in base]
    assert all(len(row) == W for row in rows)
    data = {
        "_comment": [
            "War Room map. `base` is the rendered background: exactly `height` rows of `width` characters.",
            "Coordinates are terminal cells: x = column (0 = left), y = row (0 = top). One row = 10 miles; a column is",
            "half a row (5 miles) because terminal characters are twice as tall as they are wide (config map.column_scale).",
            "region_rows: the exact region of every cell as a one-letter key (regions[].key); '~' is sea.",
            "Region `rects` are the coarse blueprint blocks (approximate: region_rows is authoritative).",
            "features: settlements. Ports have `trade` (CR/week of overseas trade, lost while blockaded) and a",
            "`harbour` sea cell where warships dock and new ships muster. sea_zones name the waters.",
            "transport: exact cells of rail, road, destroyed_rail (rubble rail bed) and trench lines.",
            "Generated by tools/generate_world_map.py (which overwrites this file).",
        ],
        "width": W,
        "height": H,
        "terrain_types": {k: {kk: vv for kk, vv in v.items() if kk != "density"} for k, v in TERRAIN_TYPES.items()},
        "regions": regions,
        "region_rows": ["".join(row) for row in grid],
        "sea_zones": SEA_ZONES,
        "features": features,
        "transport": transport,
        "base": rows,
    }
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n".join(rows))
    for f in features:
        print(f"{f['type']:<10} {f['name']:<14} {f['x']:>3},{f['y']:>3}  {f.get('harbour', '')}")


if __name__ == "__main__":
    main()
