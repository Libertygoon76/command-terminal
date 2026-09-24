from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, ListItem, ListView, Static

from src.engine.event_manager import GameOverError, ReplyError, mark_read, respond
from src.models import Email, EmailOption
from src.ui import palette
from src.ui.screens.confirm import ConfirmReplyScreen

RULE = "─" * 64
LETTERS = "ABCD"


def _status_badge(email: Email, turn: int) -> tuple[str, str]:
    """Short status text + style for the list view."""
    if email.pinned:
        return "⚠ CRITICAL", f"bold {palette.RED}"
    if email.awaiting_response:
        if email.reply_by_turn is None:
            return "◆ REPLY", palette.AMBER
        if email.reply_by_turn <= turn:
            return "◆ DUE THIS WEEK", f"bold {palette.RED}"
        return f"◆ REPLY BY WK {email.reply_by_turn:03d}", palette.AMBER
    if email.expired:
        return "✖ EXPIRED", palette.RED
    if email.replied:
        return "✔ REPLIED", palette.PHOSPHOR_DIM
    return "", ""


class MailItem(ListItem):
    def __init__(self, email: Email, turn: int) -> None:
        label = Label(self._label_text(email, turn))
        super().__init__(label)
        self.email = email
        self._label = label

    @staticmethod
    def _label_text(email: Email, turn: int) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append("● " if not email.read else "  ", style=f"bold {palette.AMBER}")
        tag = palette.CLASSIFICATION_TAG[email.classification]
        text.append(f"{tag:<2} ", style=palette.CLASSIFICATION_STYLE[email.classification])
        if not email.read:
            subject_style = f"bold {palette.PHOSPHOR_BRIGHT}"
        elif email.archived:
            subject_style = palette.PHOSPHOR_DIM
        else:
            subject_style = palette.PHOSPHOR
        text.append(email.subject.upper(), style=subject_style)
        text.append("\n     ")
        text.append(f"WK {email.received_turn:03d}  ", style=palette.PHOSPHOR_DIM)
        badge, badge_style = _status_badge(email, turn)
        if badge:
            text.append(f"{badge}  ", style=badge_style)
        text.append(email.sender, style=palette.PHOSPHOR_DIM)
        return text

    def refresh_label(self, turn: int) -> None:
        self._label.update(self._label_text(self.email, turn))


class ReplyButton(Button):
    def __init__(self, index: int, option: EmailOption) -> None:
        super().__init__(Text(f"[{LETTERS[index]}] {option.label}"), classes="reply-button")
        self.option_index = index


def render_email(email: Email, turn: int, locked: bool) -> Text:
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
    text.append(email.body + "\n", style=palette.PHOSPHOR)

    if email.arrival_consequences:
        text.append("\nIMPACT ON RECEIPT: ", style=f"bold {palette.AMBER}")
        text.append(" · ".join(email.arrival_consequences) + "\n", style=palette.PHOSPHOR_BRIGHT)

    if email.options:
        text.append("\n" + RULE + "\n", style=palette.PHOSPHOR_DIM)
        if email.replied or email.expired:
            if email.expired:
                text.append(f"DEADLINE PASSED — WEEK {email.response_turn:03d}\n", style=f"bold {palette.RED}")
                text.append("AUTOMATIC OUTCOME: ", style=palette.PHOSPHOR_DIM)
            else:
                chosen = next(i for i, o in enumerate(email.options) if o.id == email.response)
                text.append(f"REPLY TRANSMITTED — WEEK {email.response_turn:03d}\n", style=f"bold {palette.AMBER}")
                text.append(f"[{LETTERS[chosen]}] ", style=palette.AMBER)
            text.append(f"{email.response_label}\n", style=palette.PHOSPHOR_BRIGHT)
            text.append("CONSEQUENCES: ", style=palette.PHOSPHOR_DIM)
            text.append(" · ".join(email.consequences) if email.consequences else "No immediate effect.",
                        style=palette.PHOSPHOR_BRIGHT)
            text.append("\n")
        elif locked:
            text.append("TERMINAL LOCKED — NO FURTHER ORDERS CAN BE TRANSMITTED.\n", style=f"bold {palette.RED}")
        else:
            text.append("RESPONSE REQUIRED", style=f"bold {palette.AMBER}")
            if email.reply_by_turn is not None:
                due_now = email.reply_by_turn <= turn
                text.append(
                    f"  —  {'DUE THIS WEEK' if due_now else f'BY WEEK {email.reply_by_turn:03d}'}",
                    style=f"bold {palette.RED}" if due_now else palette.AMBER,
                )
            text.append("\nSelect a reply below, or press A–D.\n", style=palette.PHOSPHOR_DIM)

    text.append(f"\n{' END OF DISPATCH '.center(64, '━')}", style=style)
    return text


class InboxView(Horizontal):
    """Two-pane mail client: message list on the left, reader + reply buttons on the right."""

    BINDINGS = [
        Binding("a", "reply(0)", "Reply A–D"),
        Binding("b", "reply(1)", show=False),
        Binding("c", "reply(2)", show=False),
        Binding("d", "reply(3)", show=False),
        Binding("h", "toggle_archived", "Hide/Show Archived"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.hide_archived = False
        self._current: Email | None = None
        self._shown_ids: list[str] = []
        self._reader_signature: tuple | None = None
        self._alert_shown = False

    def compose(self) -> ComposeResult:
        game = self.app.game
        messages = self._visible_messages()
        self._shown_ids = [m.id for m in messages]
        yield ListView(*(MailItem(m, game.clock.turn) for m in messages), id="mail-list")
        with VerticalScroll(id="mail-reader"):
            yield Static(Text("NO DISPATCH SELECTED.", style=palette.PHOSPHOR_DIM), id="mail-body")
            yield Vertical(id="reply-panel")

    def on_mount(self) -> None:
        self._update_list_title()
        self.watch(self.app, "revision", self._on_revision, init=False)

    def _visible_messages(self) -> list[Email]:
        current_id = self._current.id if self._current else None
        return [
            m for m in self.app.game.inbox.newest_first()
            if not (self.hide_archived and m.archived and m.id != current_id)
        ]

    def _update_list_title(self) -> None:
        mode = "ACTIVE ONLY" if self.hide_archived else "ALL"
        self.query_one("#mail-list").border_title = f"DISPATCHES · {mode}"

    # --- selection -----------------------------------------------------------

    async def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        event.stop()
        if isinstance(event.item, MailItem):
            await self._show(event.item.email)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if isinstance(event.item, MailItem):
            await self._show(event.item.email)

    async def _show(self, email: Email) -> None:
        opened_new = self._current is None or self._current.id != email.id
        self._current = email
        await self._render_reader(force=True)
        if opened_new:
            self.query_one("#mail-reader", VerticalScroll).scroll_home(animate=False)
        if mark_read(self.app.game, email.id):
            self.app.state_changed()

    async def _render_reader(self, force: bool = False) -> None:
        game = self.app.game
        email = self._current
        locked = game.game_over is not None
        signature = None if email is None else (email.id, email.response, email.read, locked, game.clock.turn)
        if not force and signature == self._reader_signature:
            return
        self._reader_signature = signature

        body = self.query_one("#mail-body", Static)
        panel = self.query_one("#reply-panel", Vertical)
        await panel.remove_children()
        if email is None:
            body.update(Text("NO DISPATCH SELECTED.", style=palette.PHOSPHOR_DIM))
            return
        body.update(render_email(email, game.clock.turn, locked))
        if email.awaiting_response and not locked:
            await panel.mount_all(ReplyButton(i, option) for i, option in enumerate(email.options[: len(LETTERS)]))

    # --- replies -------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if isinstance(event.button, ReplyButton):
            event.stop()
            self.action_reply(event.button.option_index)

    def action_reply(self, index: int) -> None:
        email = self._current
        game = self.app.game
        if game.game_over:
            self.notify("The terminal is locked.", severity="error")
            return
        if email is None or not email.awaiting_response:
            self.notify("This dispatch does not require a reply.", severity="warning")
            return
        if index >= len(email.options):
            return
        option = email.options[index]

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._transmit(email, option)

        self.app.push_screen(ConfirmReplyScreen(email, option, LETTERS[index]), on_confirm)

    def _transmit(self, email: Email, option: EmailOption) -> None:
        try:
            changes = respond(self.app.game, email.id, option.id)
        except (ReplyError, GameOverError) as error:
            self.notify(str(error), title="TRANSMISSION REFUSED", severity="error")
            return
        self.app.state_changed()
        self.query_one("#mail-list", ListView).focus()
        self.notify("\n".join(changes) or "No immediate effect.", title="REPLY TRANSMITTED")

    async def action_toggle_archived(self) -> None:
        self.hide_archived = not self.hide_archived
        self._update_list_title()
        await self.refresh_view()

    # --- state sync ----------------------------------------------------------

    async def _on_revision(self, _revision: int) -> None:
        await self.refresh_view()

    async def refresh_view(self) -> None:
        """Sync with game state: rebuild the list only if its membership changed."""
        game = self.app.game
        turn = game.clock.turn
        mail_list = self.query_one("#mail-list", ListView)
        messages = self._visible_messages()
        ids = [m.id for m in messages]

        if ids != self._shown_ids:
            self._shown_ids = ids
            current_id = self._current.id if self._current else None
            await mail_list.clear()
            await mail_list.extend(MailItem(m, turn) for m in messages)
            if game.game_over and not self._alert_shown:
                self._alert_shown = True
                current_id = messages[0].id  # the pinned SYSTEM PURGE alert
            if ids:
                mail_list.index = ids.index(current_id) if current_id in ids else 0
            else:
                self._current = None
        else:
            for item in mail_list.query(MailItem):
                item.refresh_label(turn)
        await self._render_reader()
