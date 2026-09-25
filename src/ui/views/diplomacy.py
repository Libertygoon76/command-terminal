"""Foreign Affairs & Lend-Lease: off-map nations, trade agreements, arms purchases, convoys at sea.

Highlight a nation in the upper table: G sends an envoy with gifts, T signs (or cancels) a trade agreement.
Its lend-lease catalogue appears below: highlight a package and press B (or Enter) to buy it.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Static

from src.engine.diplomacy import (
    DiplomacyError,
    buy_lend_lease,
    cancel_trade,
    foreign,
    leaning,
    nations,
    open_port,
    packages,
    relation,
    send_envoy,
    sign_trade,
    standing,
    trade_income,
)
from src.ui import palette
from src.ui.palette import label_value

STANDING_STYLE = {"HOSTILE": f"bold {palette.RED}", "COLD": palette.RED, "NEUTRAL": palette.AMBER,
                  "FRIENDLY": palette.PHOSPHOR_BRIGHT, "ALLIED": f"bold {palette.PHOSPHOR_BRIGHT}"}


def _score(value: float) -> Text:
    label = standing(value)
    return Text(f"{value:+.0f}  {label}", style=STANDING_STYLE.get(label, palette.PHOSPHOR))


class DiplomacyView(VerticalScroll):
    BINDINGS = [
        Binding("g", "envoy", "Send Envoy"),
        Binding("t", "trade", "Trade Agreement"),
        Binding("b", "buy", "Buy Lend-Lease"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.nation_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="dip-summary", classes="summary")
        yield Static(Text("FOREIGN POWERS — G send envoy · T sign / cancel trade agreement", style=f"bold {palette.AMBER}"),
                     classes="section-title")
        yield DataTable(id="dip-nations", cursor_type="row", classes="primary-focus")
        yield Static(id="dip-detail", classes="module-status")
        yield Static(id="dip-catalog-title", classes="section-title")
        yield DataTable(id="dip-catalog", cursor_type="row")
        yield Static(Text("CONVOYS AT SEA", style=f"bold {palette.AMBER}"), classes="section-title")
        yield DataTable(id="dip-shipments", cursor_type="row")

    def on_mount(self) -> None:
        self.query_one("#dip-nations", DataTable).add_columns(
            "POWER", "LEADER", "ALIGNMENT (−VOSK … +KESTRIA)", "LEANING", "TRADE AGREEMENT", "INCOME/WK")
        self.query_one("#dip-catalog", DataTable).add_columns(
            "PACKAGE", "CONTENTS", "PRICE", "NEEDS ALIGNMENT", "AT SEA", "STATUS")
        self.query_one("#dip-shipments", DataTable).add_columns("CONVOY", "CARGO", "FROM", "ARRIVES IN", "STATUS")
        self.refresh_view()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _on_revision(self, _revision: int) -> None:
        self.refresh_view()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "dip-nations" and event.row_key is not None:
            self.nation_id = event.row_key.value
            self._render_catalog()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "dip-catalog":
            event.stop()
            self.action_buy()

    # --- actions ---------------------------------------------------------------

    def _run(self, fn, title: str, done: str) -> None:
        try:
            result = fn()
        except DiplomacyError as error:
            self.notify(str(error), title=title, severity="warning")
            return
        self.app.state_changed()
        self.notify(done.format(result=result), title=title)

    def action_envoy(self) -> None:
        if not self.nation_id:
            return
        name = nations(self.app.game)[self.nation_id]["name"]
        from src.engine.court import regency_price

        cost = regency_price(self.app.game, self.app.game.player, self.app.game.catalog["diplomacy"].get("gift_cost", 25000))
        self._run(lambda: send_envoy(self.app.game, self.nation_id), "ENVOY",
                  f"Envoy received in {name} ({cost:,} {self.app.game.currency} in gifts): alignment +{{result:.1f}} toward Kestria.")

    def action_trade(self) -> None:
        if not self.nation_id:
            return
        game = self.app.game
        name = nations(game)[self.nation_id]["name"]
        if game.player.id in foreign(game, self.nation_id)["trade"]:
            self._run(lambda: cancel_trade(game, self.nation_id), "TRADE", f"Trade agreement with {name} cancelled.")
        else:
            self._run(lambda: sign_trade(game, self.nation_id), "TRADE", f"Trade agreement signed with {name}.")

    def action_buy(self) -> None:
        table = self.query_one("#dip-catalog", DataTable)
        if not self.nation_id or table.row_count == 0:
            return
        package_id = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        self._run(lambda: buy_lend_lease(self.app.game, self.nation_id, package_id), "LEND-LEASE",
                  "{result[name]} purchased: the convoy sails ({result[weeks_left]} weeks at sea).")

    # --- rendering -------------------------------------------------------------

    def refresh_view(self) -> None:
        game = self.app.game
        cur = game.currency
        income, active, suspended = trade_income(game, game.player.id)
        port = open_port(game, game.player.id)
        summary = Text()
        summary.append_text(label_value("FOREIGN TRADE     ", f"{income:,} {cur}/WK from {len(active)} agreement(s)"))
        if suspended:
            summary.append("   SUSPENDED: EVERY PORT BLOCKADED", style=f"bold {palette.RED}")
        summary.append("\n")
        summary.append_text(label_value("CONVOY TERMINUS   ", port.upper() if port else "NONE — ALL PORTS BLOCKADED",
                                        palette.PHOSPHOR_BRIGHT if port else f"bold {palette.RED}"))
        summary.append("\n")
        summary.append_text(label_value("TREASURY          ", f"{game.player.treasury:,} {cur}"))
        if game.ceasefire_weeks:
            summary.append("\n")
            summary.append_text(label_value("CEASEFIRE        ", f"{game.ceasefire_weeks} WEEK(S) LEFT", palette.AMBER))
        self.query_one("#dip-summary", Static).update(summary)

        table = self.query_one("#dip-nations", DataTable)
        cursor = table.cursor_row
        table.clear()
        for nid, data in nations(game).items():
            treaty = game.player.id in foreign(game, nid)["trade"]
            trade_text = Text("SIGNED" + (" · SUSPENDED" if nid in suspended else ""),
                              style=f"bold {palette.RED}" if nid in suspended else palette.PHOSPHOR_BRIGHT) if treaty \
                else Text(f"— (needs {data.get('trade_min_alignment', 10):+})", style=palette.PHOSPHOR_DIM)
            table.add_row(Text(data["name"].upper(), style=palette.PHOSPHOR_BRIGHT), data.get("leader", ""),
                          _score(relation(game, nid)), Text(leaning(game, nid)),
                          trade_text, f"{data.get('trade_income_bonus_cr', 0):,}" if treaty else "—", key=nid)
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
            self.nation_id = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        self._render_catalog()

        ships = self.query_one("#dip-shipments", DataTable)
        ships.clear()
        items = {e["id"]: e for e in game.catalog["equipment"]}
        mine = [s for s in game.shipments if s["to"] == game.player.id]
        for ship in mine:
            cargo = ", ".join(f"{q:,} {items.get(i, {}).get('unit', i)}" for i, q in ship["items"].items())
            ships.add_row(ship["id"], Text(f"{ship['name']} ({cargo})"[:70]), nations(game)[ship["from"]]["name"],
                          f"{ship['weeks_left']} WK",
                          Text(ship["status"] + (f" · TORPEDOED ×{ship['losses']}" if ship["losses"] else ""),
                               style=palette.RED if ship["losses"] or "BLOCKADED" in ship["status"] else palette.PHOSPHOR))
        if not mine:
            ships.add_row("—", Text("No convoys at sea.", style=palette.PHOSPHOR_DIM), "", "", "")

    def _render_catalog(self) -> None:
        game = self.app.game
        if not self.nation_id:
            return
        data = nations(game)[self.nation_id]
        items = {e["id"]: e for e in game.catalog["equipment"]}
        detail = Text()
        detail.append(f"▸ {data['name'].upper()}  ", style=f"bold {palette.PHOSPHOR_BRIGHT}")
        detail.append(data.get("description", ""), style=palette.PHOSPHOR_DIM)
        self.query_one("#dip-detail", Static).update(detail)
        title = Text(f"LEND-LEASE FROM {data['name'].upper()} — B or ENTER to buy (paid now, delivered by sea to an open port)",
                     style=f"bold {palette.AMBER}")
        self.query_one("#dip-catalog-title", Static).update(title)
        catalog = self.query_one("#dip-catalog", DataTable)
        cursor = catalog.cursor_row
        catalog.clear()
        rel = relation(game, self.nation_id)
        for pack in packages(game, self.nation_id):
            contents = ", ".join(f"{q:,} {items.get(i, {}).get('name', i)}" for i, q in pack["items"].items())
            ok = rel >= pack["min_alignment"]
            from src.engine.court import lend_lease_price, regency_price

            match = lend_lease_price(game, self.nation_id, pack["cost"])
            price = regency_price(game, game.player, match)
            affordable = game.player.treasury >= price
            status = Text("AVAILABLE" if ok and affordable else ("TOO EXPENSIVE" if ok else "RELATIONS TOO LOW"),
                          style=palette.PHOSPHOR_BRIGHT if ok and affordable else palette.RED)
            catalog.add_row(Text(pack["name"].upper(), style=palette.PHOSPHOR_BRIGHT), Text(contents[:60]),
                            f"{price:,} {game.currency}" + (" (ROYAL MATCH)" if match != pack["cost"] else "")
                            + (" (REGENCY)" if price != match else ""),
                            f"{pack['min_alignment']:+.0f}", f"{pack['weeks']} WK",
                            status, key=pack["id"])
        if catalog.row_count:
            catalog.move_cursor(row=min(cursor, catalog.row_count - 1))
