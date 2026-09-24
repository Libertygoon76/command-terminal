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

from src.engine.map_overlay import FRIENDLY, HOSTILE, Marker, build_markers, marker_at
from src.models import WorldMap
from src.ui import palette

BORDER_CHARS = set("─│┌┐└┘├┤┬┴┼")
RAIL_CHARS = set("═║╝")
FEATURE_STYLES = {
    "★": Style(color="#ffd24a", bold=True),
    "◉": Style(color="#e8e8e8", bold=True),
    "⊕": Style(color="#e8e8e8", bold=True),
}

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

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.markers: list[Marker] = []
        self._rows: list[list[tuple[str, Style]]] = []

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

        for marker in self.markers:
            color = side_colors.get(marker.side, mixed_color)
            bg = OWNER_BG.get(world.owner_at(marker.x, marker.y), OWNER_BG[None])
            style = Style(color=color, bgcolor=bg, bold=True)
            for i, ch in enumerate(marker.text):
                x = marker.x - 1 + i
                if 0 <= x < world.width:
                    rows[marker.y][x] = (ch, style)
        self._rows = rows
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
        if ch == "╌":
            return Style(color="#7a2a2a", bgcolor=bg)  # destroyed rail in no-man's-land
        if ch == "┆":
            return Style(color="#6a5a2a", bgcolor=bg)  # trench lines
        if ch == "≈":
            return Style(color="#2aa0a0", bgcolor=bg)
        if ch == "^":
            return Style(color="#8a8f86", bgcolor=bg)
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
        on_cursor_marker = marker_at(self.markers, cx, cy)
        row = self._rows[map_y]
        segments: list[Segment] = []
        for x, (ch, style) in enumerate(row):
            if map_y == cy and (x == cx or (on_cursor_marker and on_cursor_marker.covers(x, map_y))):
                style = CURSOR
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
