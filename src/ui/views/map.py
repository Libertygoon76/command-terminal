from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.ui import palette

# Regex → style applied over the ASCII map.
MAP_HIGHLIGHTS = (
    (r"~", palette.SEA),
    (r"\[K\]", f"bold {palette.PHOSPHOR_BRIGHT}"),
    (r"\[V\]", f"bold {palette.RED}"),
    (r"\[N\]", f"bold {palette.CYAN}"),
    (r"\[ - \]", f"bold {palette.AMBER}"),
    (r"\*", f"bold {palette.AMBER}"),
    (r"╫", palette.AMBER),
)

OWNER_STYLE = {
    "kestria": palette.PHOSPHOR_BRIGHT,
    "vosk": palette.RED,
    "neutral": palette.CYAN,
    "contested": palette.AMBER,
}


class MapView(VerticalScroll):
    """Static ASCII strategic map. Dynamic ownership and unit positions arrive with logistics (Phase 5)."""

    def compose(self) -> ComposeResult:
        yield Static(id="map-ascii")
        yield Static(Text("REGIONS", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="map-regions", cursor_type="row")

    def on_mount(self) -> None:
        self.query_one("#map-regions", DataTable).add_columns("REGION", "CONTROL", "TERRAIN", "NOTES")
        self.refresh_view()

    def refresh_view(self) -> None:
        world = self.app.game.catalog["map"]
        nations = self.app.game.nations

        art = Text("\n".join(world["ascii"]), style=palette.PHOSPHOR_DIM)
        for pattern, style in MAP_HIGHLIGHTS:
            art.highlight_regex(pattern, style)
        self.query_one("#map-ascii", Static).update(art)

        table = self.query_one("#map-regions", DataTable)
        table.clear()
        for region in world["regions"]:
            owner = region["owner"]
            owner_name = nations[owner].name if owner in nations else owner.upper()
            table.add_row(
                region["name"].upper(),
                Text(owner_name.upper(), style=OWNER_STYLE.get(owner, palette.PHOSPHOR)),
                region["terrain"].upper(),
                "CAPITAL" if region.get("capital") else "",
            )
