from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Button, ContentSwitcher, Footer, ListView, Static

from src.engine.dilemmas import DilemmaError, card, resolve
from src.engine.event_manager import GameOverError
from src.engine.tick_engine import DilemmaPendingError
from src.ui import palette
from src.ui.screens.dilemma import DilemmaScreen
from src.ui.screens.game_over import GameOverScreen
from src.ui.views import AirView, CitiesView, CourtView, DiplomacyView, EconomyView, InboxView, MapView, MilitaryView, ResearchView
from src.ui.widgets.sidebar import NAV_ENTRIES, NavItem, Sidebar
from src.ui.widgets.status_bar import StatusBar

VIEW_TITLES = {view_id: label for view_id, label, _ in NAV_ENTRIES}


class TerminalScreen(Screen):
    """The command desktop: status bar, navigation sidebar, switchable main display, comms log.

    The screen only issues commands (advance week, switch view). Widgets redraw themselves
    by watching `app.revision`.
    """

    BINDINGS = [
        Binding("1", "show('inbox')", "Inbox"),
        Binding("2", "show('economy')", "Economy"),
        Binding("3", "show('military')", "Military"),
        Binding("4", "show('map')", "Map"),
        Binding("5", "show('research')", "Research"),
        Binding("6", "show('air')", "Air"),
        Binding("7", "show('diplomacy')", "Diplomacy"),
        Binding("8", "show('cities')", "Cities"),
        Binding("0", "show('court')", "Royal Court"),
        Binding("n", "end_turn", "Advance Week"),
        Binding("ctrl+s,f5", "save_game", "Save"),
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
                yield ResearchView(id="research")
                yield AirView(id="air")
                yield DiplomacyView(id="diplomacy")
                yield CitiesView(id="cities")
                yield CourtView(id="court")
        yield Static(id="comms-log")
        yield Footer()

    def on_mount(self) -> None:
        self._set_view_title("inbox")
        self._log(f"SESSION OPENED. {self.app.game.inbox.unread_count} UNREAD DISPATCH(ES).")
        self.query_one("#mail-list", ListView).focus()
        if self.app.game.pending_dilemma:
            self.call_after_refresh(self.show_dilemma)

    # --- navigation ----------------------------------------------------------

    def action_show(self, view_id: str) -> None:
        self.query_one("#main-view", ContentSwitcher).current = view_id
        self._set_view_title(view_id)
        self.query_one(Sidebar).highlight(view_id)
        focus_target = self.query_one(f"#{view_id}").query(".primary-focus, ListView, DataTable").first()
        focus_target.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, NavItem):
            self.action_show(event.item.view_id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "advance-week":
            self.action_end_turn()

    def _set_view_title(self, view_id: str) -> None:
        self.query_one("#main-view").border_title = VIEW_TITLES[view_id]

    # --- turn advance --------------------------------------------------------

    def action_end_turn(self) -> None:
        game = self.app.game
        try:
            report = self.app.tick_engine.advance()
        except GameOverError as error:
            self.notify(str(error), title="TERMINAL LOCKED", severity="error")
            return
        except DilemmaPendingError:
            self.show_dilemma()
            return
        self.app.state_changed()

        summary = " ".join(report.log) or "NO NEW REPORTS."
        self._log(summary.upper())

        if report.game_over:
            self.action_show("inbox")
            alert = next((m for m in game.inbox.newest_first() if m.pinned), None)
            victory = report.game_over.cause == "victory"
            self.app.push_screen(GameOverScreen(alert.subject if alert else "SYSTEM PURGE",
                                                alert.body if alert else "The government has fallen.",
                                                victory=victory))
            return

        awaiting = game.inbox.awaiting_response()
        due_now = sum(1 for m in awaiting if m.reply_by_turn == game.clock.turn)
        lines = [summary]
        if due_now:
            lines.append(f"{due_now} DISPATCH(ES) MUST BE ANSWERED THIS WEEK.")
        self.notify(
            "\n".join(lines),
            title=f"WEEK {report.turn:03d} · {report.date}",
            severity="warning" if due_now else "information",
        )
        if game.pending_dilemma:
            self.show_dilemma()

    # --- save ------------------------------------------------------------------

    def action_save_game(self) -> None:
        from src.engine.savegame import SaveError, save_game

        try:
            path = save_game(self.app.game, self.app.save_path)
        except (SaveError, OSError) as error:
            self.notify(str(error), title="SAVE FAILED", severity="error")
            return
        clock = self.app.game.clock
        self._log(f"CAMPAIGN SAVED: WEEK {clock.turn:03d} → {path.name}")
        self.notify(f"Week {clock.turn:03d} ({clock.date_str}) written to {path}.\n"
                    "Resume with: python main.py --load", title="CAMPAIGN SAVED")

    # --- classified dilemmas ---------------------------------------------------

    def show_dilemma(self) -> None:
        game = self.app.game
        if not game.pending_dilemma or isinstance(self.app.screen, DilemmaScreen):
            return

        def decided(choice_id: str | None) -> None:
            if choice_id is None:
                return
            title = card(game, game.pending_dilemma)["title"] if game.pending_dilemma else "DILEMMA"
            try:
                changes = resolve(game, choice_id)
            except (DilemmaError, GameOverError) as error:
                self.notify(str(error), title="DILEMMA", severity="error")
                return
            self.app.state_changed()
            self._log(f"DECISION RECORDED: {title.upper()}.")
            self.notify("\n".join(changes) or "No immediate consequences.", title=f"DECISION: {title.upper()}")
            if game.pending_dilemma:  # another emergency is waiting behind this one
                self.call_after_refresh(self.show_dilemma)

        self.app.push_screen(DilemmaScreen(game.pending_dilemma), decided)

    # --- helpers -------------------------------------------------------------

    def _log(self, message: str) -> None:
        clock = self.app.game.clock
        over = self.app.game.game_over
        fallen = over is not None and over.cause != "victory"
        line = Text()
        line.append(" COMMS ", style=f"bold {palette.BACKGROUND} on {palette.RED if fallen else palette.PHOSPHOR_DIM}")
        line.append(f" WK {clock.turn:03d} // {clock.date_str} » ", style=palette.PHOSPHOR_DIM)
        line.append(message, style=palette.RED if fallen else palette.PHOSPHOR)
        self.query_one("#comms-log", Static).update(line)
