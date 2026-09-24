"""The War Room map: a pannable Line-API widget drawing the ASCII base map plus the unit overlay."""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.binding import Binding
from textual.geometry import Region, Size
from textual.message import Message
from textual.reactive import var
from textual.scroll_view import ScrollView
from textual.strip import Strip

from src.engine.logistics_engine import player_supply_picture
from src.engine.map_overlay import FRIENDLY, HOSTILE, Marker, build_markers, marker_at
from src.models import WorldMap
from src.ui import palette

BORDER_CHARS = set("─│┌┐└┘├┤┬┴┼")
RAIL_CHARS = set("═║╝")
ROAD_CHARS = set("┈┊")
SUPPLY_BG = "#062238"  # cells inside our supply network (overlay)
ZOC_BG = "#3a0a0a"  # cells under enemy zone of control (overlay)
FEATURE_STYLES = {
    "★": Style(color="#ffd24a", bold=True),
    "◉": Style(color="#e8e8e8", bold=True),
    "⊕": Style(color="#e8e8e8", bold=True),
    "▣": Style(color="#d0b070", bold=True),
    "○": Style(color="#b8b8b8"),
}
BLOCKADED = Style(color="#ff3b3b", bold=True, blink=False)

# Background tint per region owner: the situation-map "colour wash" of territory.
OWNER_BG = {
    None: "#02070c",  # sea
    "kestria": "#051009",
    "vosk": "#120505",
    "contested": "#110d03",
    "neutral": "#03101a",
}
OWNER_TEXT = {
    None: "#3a8fb0",
    "kestria": palette.PHOSPHOR,
    "vosk": "#ff7a7a",
    "contested": palette.AMBER,
    "neutral": "#7fe0ff",
}
OWNER_TERRAIN = {
    None: "#1f5670",
    "kestria": "#2f6f40",
    "vosk": "#7a3030",
    "contested": "#7a5a18",
    "neutral": "#2f6a78",
}
CROSSHAIR_BG = "#0f2a18"
CURSOR = Style(color="#000000", bgcolor=palette.AMBER, bold=True)
TARGET_CURSOR = Style(color="#000000", bgcolor="#4fd8ff", bold=True)
GHOST_COLOR = "#8a3a3a"
DESTINATION_GLYPH = "◇"
ROUTE_GLYPH = "·"


class MapCanvas(ScrollView, can_focus=True):
    """Draws `world.base`, colours it by terrain and ownership, and overlays unit markers.

    Arrow keys move a cursor (the view follows it); the mouse wheel pans; clicking selects
    a cell. `CursorMoved` is posted whenever the cursor lands on a new cell.
    """

    BINDINGS = [
        Binding("up", "move(0, -1)", "Cursor", show=False),
        Binding("down", "move(0, 1)", show=False),
        Binding("left", "move(-1, 0)", show=False),
        Binding("right", "move(1, 0)", show=False),
        Binding("shift+up", "move(0, -5)", show=False),
        Binding("shift+down", "move(0, 5)", show=False),
        Binding("shift+left", "move(-5, 0)", show=False),
        Binding("shift+right", "move(5, 0)", show=False),
        Binding("right_square_bracket", "cycle(1)", "Next Unit"),
        Binding("left_square_bracket", "cycle(-1)", "Prev Unit"),
        Binding("c", "center", "Center"),
    ]

    class CursorMoved(Message):
        def __init__(self, x: int, y: int, marker: Marker | None) -> None:
            super().__init__()
            self.x = x
            self.y = y
            self.marker = marker

    cursor: var[tuple[int, int]] = var((0, 0), init=False)
    targeting: var[bool] = var(False, init=False)  # choosing a move-order destination
    supply_overlay: var[bool] = var(False, init=False)  # tint our supply net and enemy ZOC

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.markers: list[Marker] = []
        self._rows: list[list[tuple[str, Style]]] = []
        self._occupied: set[tuple[int, int]] = set()  # cells covered by markers
        self._supply_view: tuple[set, set] = (set(), set())  # (our net, known enemy ZOC) for the overlay
        self.route_preview: list[tuple[int, int]] = []  # path drawn for the selected/targeting unit

    def show_route(self, path: list[tuple[int, int]]) -> None:
        self.route_preview = list(path)
        self.refresh()

    def watch_targeting(self, _old: bool, _new: bool) -> None:
        self.refresh()

    def watch_supply_overlay(self, _old: bool, _new: bool) -> None:
        self.refresh()

    @property
    def world(self) -> WorldMap:
        return self.app.game.world_map

    # --- lifecycle -----------------------------------------------------------

    def on_mount(self) -> None:
        self.virtual_size = Size(self.world.width, self.world.height)
        self.rebuild()
        capital = next((f for f in self.world.features if f.type == "capital"), None)
        start = self.markers[0] if self.markers else None
        self.cursor = (start.x, start.y) if start else ((capital.x, capital.y) if capital else (0, 0))
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.rebuild()
        self._announce()

    def rebuild(self) -> None:
        """Recompute markers and the styled cell grid from game state."""
        game = self.app.game
        self.markers = build_markers(game)
        cfg = game.config.get("map", {})
        side_colors = {
            FRIENDLY: cfg.get("friendly_color", "#4fd8ff"),
            HOSTILE: cfg.get("hostile_color", "#ff3b3b"),
        }
        mixed_color = cfg.get("mixed_stack_color", palette.AMBER)

        world = self.world
        rows = []
        for y, line in enumerate(world.base):
            row = []
            for x, ch in enumerate(line):
                row.append((ch, self._terrain_style(world, x, y, ch)))
            rows.append(row)

        occupied: set[tuple[int, int]] = set()
        for marker in self.markers:
            color = GHOST_COLOR if marker.ghost else side_colors.get(marker.side, mixed_color)
            bg = OWNER_BG.get(world.owner_at(marker.x, marker.y), OWNER_BG[None])
            style = Style(color=color, bgcolor=bg, bold=not marker.ghost)
            for i, ch in enumerate(marker.text):
                x = marker.x - 1 + i
                if 0 <= x < world.width:
                    rows[marker.y][x] = (ch, style)
                    occupied.add((x, marker.y))

        # Standing move orders: a destination diamond for every friendly formation on the march.
        friendly_color = side_colors[FRIENDLY]
        for unit in game.player.units:
            order = unit.active_order
            if order and (order.x, order.y) not in occupied:
                bg = OWNER_BG.get(world.owner_at(order.x, order.y), OWNER_BG[None])
                rows[order.y][order.x] = (DESTINATION_GLYPH, Style(color=friendly_color, bgcolor=bg, bold=True))
        for port in world.ports():  # blockaded harbours glow red
            if port.name in game.blockades and (port.x, port.y) not in occupied:
                bg = OWNER_BG.get(world.owner_at(port.x, port.y), OWNER_BG[None])
                rows[port.y][port.x] = ("⊕", BLOCKADED + Style(bgcolor=bg))
        self._rows = rows
        self._occupied = occupied
        self._supply_view = player_supply_picture(game)
        self.refresh()

    @staticmethod
    def _terrain_style(world: WorldMap, x: int, y: int, ch: str) -> Style:
        owner = world.owner_at(x, y)
        bg = OWNER_BG.get(owner, OWNER_BG["neutral"])
        if ch in FEATURE_STYLES:
            return FEATURE_STYLES[ch] + Style(bgcolor=bg)
        if ch in BORDER_CHARS:
            color = "#c08a10" if world.is_national_border(x, y) else "#1f6f35"
            return Style(color=color, bgcolor=bg)
        if ch in RAIL_CHARS:
            return Style(color="#9a7a3a", bgcolor=bg)
        if ch in ROAD_CHARS:
            return Style(color="#7a6a48", bgcolor=bg)
        if ch == "╌":
            return Style(color="#7a2a2a", bgcolor=bg)  # destroyed rail in no-man's-land
        if ch == "┆":
            return Style(color="#6a5a2a", bgcolor=bg)  # trench lines
        if ch == "≈":
            return Style(color="#2aa0a0", bgcolor=bg)
        if ch == "^":
            return Style(color="#8a8f86", bgcolor=bg)
        if ch == "∩":
            return Style(color="#8a7a5a", bgcolor=bg)
        if ch == "~":
            return Style(color="#1f5670", bgcolor=bg)
        if ch.isalnum() or ch in "-/":
            return Style(color=OWNER_TEXT.get(owner, palette.PHOSPHOR), bgcolor=bg, bold=ch.isupper())
        return Style(color=OWNER_TERRAIN.get(owner, palette.PHOSPHOR_DIM), bgcolor=bg)

    # --- rendering -----------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        map_y = y + scroll_y
        width = self.size.width
        blank = Style(bgcolor=palette.BACKGROUND)
        if not 0 <= map_y < len(self._rows):
            return Strip.blank(width, blank)

        cx, cy = self.cursor
        on_cursor_marker = None if self.targeting else marker_at(self.markers, cx, cy)
        cursor_style = TARGET_CURSOR if self.targeting else CURSOR
        route_color = self.app.game.config.get("map", {}).get("friendly_color", "#4fd8ff")
        route_cells = {c for c in self.route_preview if c[1] == map_y and c not in self._occupied}
        network, zoc = self._supply_view if self.supply_overlay else (frozenset(), frozenset())
        row = self._rows[map_y]
        segments: list[Segment] = []
        for x, (ch, style) in enumerate(row):
            if self.supply_overlay:
                if (x, map_y) in zoc:
                    style = style + Style(bgcolor=ZOC_BG)
                elif (x, map_y) in network:
                    style = style + Style(bgcolor=SUPPLY_BG)
            if (x, map_y) in route_cells:
                is_end = (x, map_y) == self.route_preview[-1]
                ch = DESTINATION_GLYPH if is_end else ROUTE_GLYPH
                style = style + Style(color=route_color, bold=is_end)
            if map_y == cy and (x == cx or (on_cursor_marker and on_cursor_marker.covers(x, map_y))):
                style = cursor_style
            elif (map_y == cy or x == cx) and self.has_focus:
                style = style + Style(bgcolor=CROSSHAIR_BG)
            if segments and segments[-1].style == style:
                segments[-1] = Segment(segments[-1].text + ch, style)
            else:
                segments.append(Segment(ch, style))
        return Strip(segments, len(row)).crop_extend(scroll_x, scroll_x + width, blank)

    # --- cursor --------------------------------------------------------------

    def watch_cursor(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        x, y = new
        self.scroll_to_region(Region(x - 6, y - 3, 13, 7), animate=False, immediate=True)
        self.refresh()
        self._announce()

    def _announce(self) -> None:
        x, y = self.cursor
        self.post_message(self.CursorMoved(x, y, marker_at(self.markers, x, y)))

    def jump_to(self, x: int, y: int) -> None:
        world = self.world
        self.cursor = (max(0, min(world.width - 1, x)), max(0, min(world.height - 1, y)))

    def action_move(self, dx: int, dy: int) -> None:
        x, y = self.cursor
        self.jump_to(x + dx, y + dy)

    def action_cycle(self, step: int) -> None:
        """Jump between unit markers in reading order."""
        if not self.markers:
            return
        ordered = sorted(self.markers, key=lambda m: (m.y, m.x))
        x, y = self.cursor
        current = marker_at(ordered, x, y)
        if current in ordered:
            index = (ordered.index(current) + step) % len(ordered)
        else:
            after = [i for i, m in enumerate(ordered) if (m.y, m.x) > (y, x)]
            index = (after[0] if after else 0) if step > 0 else ((after[0] - 1) if after else -1)
        target = ordered[index]
        self.jump_to(target.x, target.y)

    def action_center(self) -> None:
        x, y = self.cursor
        self.scroll_to(x - self.size.width // 2, y - self.size.height // 2, animate=False)

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None:
            return
        scroll_x, scroll_y = self.scroll_offset
        self.focus()
        self.jump_to(offset.x + scroll_x, offset.y + scroll_y)

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()
