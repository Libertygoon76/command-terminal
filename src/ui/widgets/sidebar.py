from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from src.ui import palette

# (view id, label, hotkey) — view ids match the ContentSwitcher children in TerminalScreen.
NAV_ENTRIES = (
    ("inbox", "INBOX", "1"),
    ("economy", "ECONOMY", "2"),
    ("military", "MILITARY", "3"),
    ("map", "STRATEGIC MAP", "4"),
)


class NavItem(ListItem):
    def __init__(self, view_id: str, label: str, hotkey: str) -> None:
        text_label = Label(self._label_text(label, hotkey, 0))
        super().__init__(text_label)
        self.view_id = view_id
        self.base_label = label
        self.hotkey = hotkey
        self._label = text_label

    @staticmethod
    def _label_text(label: str, hotkey: str, count: int) -> Text:
        text = Text()
        text.append(f"[{hotkey}] ", style=palette.PHOSPHOR_DIM)
        text.append(label)
        if count:
            text.append(f"  ({count})", style=f"bold {palette.AMBER}")
        return text

    def set_badge(self, count: int) -> None:
        self._label.update(self._label_text(self.base_label, self.hotkey, count))


class Sidebar(Vertical):
    def compose(self) -> ComposeResult:
        yield ListView(*(NavItem(*entry) for entry in NAV_ENTRIES), id="nav-list")
        yield Static(id="sys-info")

    def on_mount(self) -> None:
        self.border_title = "SYS // NAV"
        self.refresh_counts()

    def refresh_counts(self) -> None:
        game = self.app.game
        for item in self.query(NavItem):
            if item.view_id == "inbox":
                item.set_badge(game.inbox.unread_count)

        info = Text()
        info.append("TERMINAL\n", style=palette.PHOSPHOR_DIM)
        info.append(f"{game.config.get('terminal_designation', '')}\n\n", style=palette.PHOSPHOR)
        info.append("OPERATOR\n", style=palette.PHOSPHOR_DIM)
        info.append(f"{game.player.leader_title.upper()}\n\n", style=palette.PHOSPHOR)
        info.append("CLEARANCE\n", style=palette.PHOSPHOR_DIM)
        info.append("OMEGA\n\n", style=f"bold {palette.RED}")
        info.append("AWAITING REPLY\n", style=palette.PHOSPHOR_DIM)
        info.append(f"{game.inbox.awaiting_response_count}", style=f"bold {palette.AMBER}")
        self.query_one("#sys-info", Static).update(info)

    def highlight(self, view_id: str) -> None:
        nav = self.query_one("#nav-list", ListView)
        for index, (entry_id, _, _) in enumerate(NAV_ENTRIES):
            if entry_id == view_id:
                nav.index = index
                return
