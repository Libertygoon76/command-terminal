from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import ContentSwitcher, DataTable, Footer, ListView, Static

from src.ui import palette
from src.ui.views import EconomyView, InboxView, MapView, MilitaryView
from src.ui.widgets.sidebar import NAV_ENTRIES, NavItem, Sidebar
from src.ui.widgets.status_bar import StatusBar

VIEW_TITLES = {view_id: label for view_id, label, _ in NAV_ENTRIES}


class TerminalScreen(Screen):
    """The command desktop: status bar, navigation sidebar, switchable main display, comms log."""

    BINDINGS = [
        Binding("1", "show('inbox')", "Inbox"),
        Binding("2", "show('economy')", "Economy"),
        Binding("3", "show('military')", "Military"),
        Binding("4", "show('map')", "Map"),
        Binding("n", "end_turn", "End Turn"),
        Binding("q", "app.quit", "Log Out"),
    ]

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Horizontal(id="workspace"):
            yield Sidebar(id="sidebar")
            with ContentSwitcher(initial="inbox", id="main-view"):
                yield InboxView(id="inbox")
                yield EconomyView(id="economy")
                yield MilitaryView(id="military")
                yield MapView(id="map")
        yield Static(id="comms-log")
        yield Footer()

    def on_mount(self) -> None:
        self._set_view_title("inbox")
        self._log(f"SESSION OPENED. {self.app.game.inbox.unread_count} UNREAD DISPATCH(ES).")
        self.query_one("#mail-list", ListView).focus()

    # --- navigation ----------------------------------------------------------

    def action_show(self, view_id: str) -> None:
        self.query_one("#main-view", ContentSwitcher).current = view_id
        self._set_view_title(view_id)
        self.query_one(Sidebar).highlight(view_id)
        focus_target = self.query_one(f"#{view_id}").query("ListView, DataTable").first()
        focus_target.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, NavItem):
            self.action_show(event.item.view_id)

    def _set_view_title(self, view_id: str) -> None:
        self.query_one("#main-view").border_title = VIEW_TITLES[view_id]

    # --- turn advance --------------------------------------------------------

    async def action_end_turn(self) -> None:
        report = self.app.tick_engine.advance()

        self.query_one(StatusBar).refresh_status()
        await self.query_one(InboxView).refresh_view()
        self.query_one(EconomyView).refresh_view()
        self.query_one(MilitaryView).refresh_view()
        self.query_one(MapView).refresh_view()
        self.query_one(Sidebar).refresh_counts()

        summary = " ".join(report.log) or "NO NEW REPORTS."
        self._log(summary.upper())
        self.notify(
            f"WEEK {report.turn:03d} · {report.date}\n{summary}",
            title="TURN ADVANCED",
            severity="warning" if report.new_messages else "information",
        )

    # --- inbox events --------------------------------------------------------

    def on_inbox_view_mail_opened(self, event: InboxView.MailOpened) -> None:
        self.query_one(Sidebar).refresh_counts()

    # --- helpers -------------------------------------------------------------

    def _log(self, message: str) -> None:
        clock = self.app.game.clock
        line = Text()
        line.append(" COMMS ", style=f"bold {palette.BACKGROUND} on {palette.PHOSPHOR_DIM}")
        line.append(f" WK {clock.turn:03d} // {clock.date_str} » ", style=palette.PHOSPHOR_DIM)
        line.append(message, style=palette.PHOSPHOR)
        self.query_one("#comms-log", Static).update(line)
