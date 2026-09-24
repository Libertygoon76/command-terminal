"""The City Inspector: every Kestrian city, its dashboard, construction and local news wire.

Highlight a city: its dashboard and news appear below. H builds a Hospital, B a Bunker Complex, I Local
Industry (paid now; one project is built at a time); X cancels the last project in the queue (half refunded).
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import DataTable, Static

from src.engine.cities import CityError, buildings, cancel_building, city_region, order_building, situation
from src.ui import palette

TAG_TEXT = {"infected": ("[INFECTED]", f"bold {palette.RED}"), "quarantined": ("[QUARANTINE]", palette.AMBER),
            "battle_near": ("[BATTLE NEARBY]", f"bold {palette.RED}"), "enemy_near": ("[ENEMY NEAR]", palette.AMBER),
            "blockaded": ("[BLOCKADED]", f"bold {palette.RED}"), "disaster": ("[DAMAGED]", palette.AMBER),
            "low_morale": ("[UNREST]", f"bold {palette.RED}")}
TYPE_LABEL = {"capital": "CAPITAL", "city": "CITY", "industrial": "INDUSTRY", "port": "PORT", "town": "TOWN"}


class CitiesView(VerticalScroll):
    BINDINGS = [
        Binding("h", "build('hospital')", "Hospital"),
        Binding("b", "build('bunker')", "Bunker"),
        Binding("i", "build('local_industry')", "Industry"),
        Binding("x", "cancel", "Cancel Project"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.city_name: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="city-summary", classes="summary")
        yield Static(Text("KESTRIAN CITIES — highlight one to inspect it · H hospital · B bunker · I industry · "
                          "X cancel", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="city-table", cursor_type="row", classes="primary-focus")
        with Horizontal(id="city-panels"):
            yield Static(id="city-detail")
            with VerticalScroll(id="city-news-scroll"):
                yield Static(id="city-news")

    def on_mount(self) -> None:
        self.query_one("#city-table", DataTable).add_columns(
            "CITY", "TYPE", "POPULATION", "LOCAL MORALE", "BUILDINGS", "UNDER CONSTRUCTION", "SITUATION")
        self.query_one("#city-news-scroll").border_title = "LOCAL NEWS WIRE"
        self.refresh_view()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None:
            self.city_name = event.row_key.value
            self._render_city()

    # --- actions ---------------------------------------------------------------

    def action_build(self, building: str) -> None:
        if not self.city_name:
            return
        game = self.app.game
        try:
            project = order_building(game, self.city_name, building)
        except CityError as error:
            self.notify(str(error), title="CONSTRUCTION", severity="warning")
            return
        self.app.state_changed()
        spec = buildings(game)[building]
        queued = len(game.cities[self.city_name].construction_queue)
        self.notify(f"{spec['name']} ordered in {self.city_name} ({project['cost']:,} {game.currency}, "
                    f"{project['weeks_total']} weeks{', queued' if queued > 1 else ''}).", title="CONSTRUCTION")

    def action_cancel(self) -> None:
        if not self.city_name:
            return
        try:
            project = cancel_building(self.app.game, self.city_name)
        except CityError as error:
            self.notify(str(error), title="CONSTRUCTION", severity="warning")
            return
        self.app.state_changed()
        self.notify(f"{buildings(self.app.game)[project['building']]['name']} cancelled; half the cost refunded.",
                    title="CONSTRUCTION")

    # --- rendering -------------------------------------------------------------

    def refresh_view(self) -> None:
        game = self.app.game
        cities = sorted(game.cities.values(), key=lambda c: (-c.population, c.name))
        total = sum(c.population for c in cities)
        unrest = sum(1 for c in cities if c.local_morale < game.catalog["cities"].get("unrest_below", 15))
        summary = Text()
        summary.append(f"CITIES {len(cities)}   URBAN POPULATION {total:,}   ", style=palette.PHOSPHOR)
        summary.append(f"IN UNREST {unrest}", style=f"bold {palette.RED}" if unrest else palette.PHOSPHOR_DIM)
        building_now = sum(1 for c in cities if c.construction_queue)
        summary.append(f"   BUILDING {building_now}", style=palette.AMBER if building_now else palette.PHOSPHOR_DIM)
        self.query_one("#city-summary", Static).update(summary)

        table = self.query_one("#city-table", DataTable)
        cursor = table.cursor_row
        table.clear()
        names = {k: v["name"] for k, v in buildings(game).items()}
        for city in cities:
            tags = situation(game, city)
            situation_text = Text()
            for tag in tags:
                if tag in TAG_TEXT:
                    situation_text.append(TAG_TEXT[tag][0] + " ", style=TAG_TEXT[tag][1])
            morale_style = palette.MORALE_STYLE.get(
                "COLLAPSING" if city.local_morale < 20 else "UNREST" if city.local_morale < 40 else
                "STEADY" if city.local_morale < 70 else "HIGH", palette.PHOSPHOR)
            project = city.construction_queue[0] if city.construction_queue else None
            table.add_row(
                Text(city.name.upper(), style=palette.PHOSPHOR_BRIGHT),
                TYPE_LABEL.get(city.type, city.type.upper()),
                f"{city.population:,}",
                Text(f"{city.local_morale:.0f}%  {palette.meter(city.local_morale, width=8)}", style=morale_style),
                ", ".join(names[b] for b in city.buildings) or "—",
                f"{names[project['building']]} · {project['weeks_left']} WK" if project else "—",
                situation_text or Text("quiet", style=palette.PHOSPHOR_DIM),
                key=city.name,
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
            self.city_name = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        self._render_city()

    def _render_city(self) -> None:
        game = self.app.game
        city = game.cities.get(self.city_name or "")
        if city is None:
            return
        specs = buildings(game)
        detail = Text()
        detail.append(f"▸ {city.name.upper()}", style=f"bold {palette.PHOSPHOR_BRIGHT}")
        detail.append(f"  {TYPE_LABEL.get(city.type, city.type)} · {city_region(game, city).upper()}\n\n",
                      style=palette.PHOSPHOR_DIM)
        detail.append(f"POPULATION     {city.population:,}\n", style=palette.PHOSPHOR)
        detail.append(f"LOCAL MORALE   {city.local_morale:.0f}%  {palette.meter(city.local_morale)}\n", style=palette.PHOSPHOR)
        detail.append(f"BUILDINGS      {', '.join(specs[b]['name'] for b in city.buildings) or 'none'}\n\n",
                      style=palette.PHOSPHOR)
        detail.append("CONSTRUCTION QUEUE\n", style=f"bold {palette.AMBER}")
        if city.construction_queue:
            for i, p in enumerate(city.construction_queue):
                done = 1 - p["weeks_left"] / max(1, p["weeks_total"])
                state = f"{palette.meter(done * 100, width=10)} {p['weeks_left']} WK LEFT" if i == 0 else "QUEUED"
                detail.append(f"  {specs[p['building']]['name']:<18} {state}\n", style=palette.PHOSPHOR_BRIGHT)
        else:
            detail.append("  idle\n", style=palette.PHOSPHOR_DIM)
        detail.append("\nBUILD\n", style=f"bold {palette.AMBER}")
        for key, bid in (("H", "hospital"), ("B", "bunker"), ("I", "local_industry")):
            spec = specs[bid]
            maxed = city.count(bid) >= int(spec.get("max", 1))
            detail.append(f"  [{key}] {spec['name']:<16} {spec['cost']:>7,} {game.currency} · {spec['weeks']} WK",
                          style=palette.PHOSPHOR_DIM if maxed else palette.PHOSPHOR_BRIGHT)
            detail.append("  (built)\n" if maxed else "\n", style=palette.PHOSPHOR_DIM)
            detail.append(f"      {spec['description']}\n", style=palette.PHOSPHOR_DIM)
        self.query_one("#city-detail", Static).update(detail)

        news = Text()
        if not city.news:
            news.append("No dispatches from this city yet.", style=palette.PHOSPHOR_DIM)
        for item in reversed(city.news):
            news.append(f"WK {item['turn']:03d} ", style=palette.AMBER)
            news.append(item["text"] + "\n\n", style=palette.PHOSPHOR)
        self.query_one("#city-news", Static).update(news)
