from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static

from src.models import Email
from src.ui import palette

RULE = "─" * 64


class MailItem(ListItem):
    def __init__(self, email: Email) -> None:
        label = Label(self._label_text(email))
        super().__init__(label)
        self.email = email
        self._label = label

    @staticmethod
    def _label_text(email: Email) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append("● " if not email.read else "  ", style=f"bold {palette.AMBER}")
        tag = palette.CLASSIFICATION_TAG[email.classification]
        text.append(f"{tag:<2} ", style=palette.CLASSIFICATION_STYLE[email.classification])
        text.append(email.subject.upper(), style=f"bold {palette.PHOSPHOR_BRIGHT}" if not email.read else palette.PHOSPHOR)
        text.append("\n     ")
        text.append(f"WK {email.received_turn:03d}  ", style=palette.PHOSPHOR_DIM)
        if email.awaiting_response:
            text.append("◆ REPLY  ", style=palette.AMBER)
        text.append(email.sender, style=palette.PHOSPHOR_DIM)
        return text

    def refresh_label(self) -> None:
        self._label.update(self._label_text(self.email))


def render_email(email: Email) -> Text:
    style = palette.CLASSIFICATION_STYLE[email.classification]
    text = Text()
    text.append(f" {email.classification} ".center(64, "━"), style=style)
    text.append("\n\n")
    for field, value in (
        ("FROM", email.sender),
        ("DATE", f"{email.received_date}  (WEEK {email.received_turn:03d})"),
        ("SUBJ", email.subject.upper()),
    ):
        text.append(f"{field}:  ", style=palette.PHOSPHOR_DIM)
        text.append(value + "\n", style=palette.PHOSPHOR_BRIGHT)
    text.append(RULE + "\n\n", style=palette.PHOSPHOR_DIM)
    text.append(email.body + "\n\n", style=palette.PHOSPHOR)

    if email.options:
        text.append(RULE + "\n", style=palette.PHOSPHOR_DIM)
        text.append("RESPONSE OPTIONS\n\n", style=f"bold {palette.AMBER}")
        for letter, option in zip("ABCDEFGHIJ", email.options):
            chosen = email.response == option.id
            text.append(f"  [{letter}] ", style=palette.AMBER)
            text.append(option.label + ("  ✔ SENT" if chosen else "") + "\n", style=palette.PHOSPHOR_BRIGHT)
        text.append("\n  REPLY TRANSMISSION COMES ONLINE IN PHASE 2.\n", style=palette.PHOSPHOR_DIM)

    text.append(f"\n{' END OF DISPATCH '.center(64, '━')}", style=style)
    return text


class InboxView(Horizontal):
    """Two-pane mail client: message list on the left, reader on the right."""

    class MailOpened(Message):
        def __init__(self, email: Email) -> None:
            super().__init__()
            self.email = email

    def compose(self) -> ComposeResult:
        messages = self.app.game.inbox.newest_first()
        yield ListView(*(MailItem(m) for m in messages), id="mail-list")
        with VerticalScroll(id="mail-reader"):
            yield Static(Text("NO DISPATCH SELECTED.", style=palette.PHOSPHOR_DIM), id="mail-body")

    def on_mount(self) -> None:
        self.query_one("#mail-list").border_title = "DISPATCHES"

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        event.stop()
        if isinstance(event.item, MailItem):
            self._open(event.item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if isinstance(event.item, MailItem):
            self._open(event.item)

    def _open(self, item: MailItem) -> None:
        email = item.email
        self.query_one("#mail-body", Static).update(render_email(email))
        self.query_one("#mail-reader", VerticalScroll).scroll_home(animate=False)
        if not email.read:
            email.read = True
            item.refresh_label()
        self.post_message(self.MailOpened(email))

    async def refresh_view(self) -> None:
        """Rebuild the message list, keeping the currently open dispatch highlighted."""
        mail_list = self.query_one("#mail-list", ListView)
        current = mail_list.highlighted_child
        current_id = current.email.id if isinstance(current, MailItem) else None

        messages = self.app.game.inbox.newest_first()
        await mail_list.clear()
        await mail_list.extend(MailItem(m) for m in messages)

        ids = [m.id for m in messages]
        if ids:
            mail_list.index = ids.index(current_id) if current_id in ids else 0
