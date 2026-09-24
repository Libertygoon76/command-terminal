"""Headless UI tests: drive the real Textual app and check it reacts to state changes live."""

import asyncio
import os
from pathlib import Path

from textual.widgets import Button, ListView, Static

from src.ui.app import CommandTerminalApp
from src.ui.screens.confirm import ConfirmReplyScreen
from src.ui.screens.terminal import TerminalScreen
from src.ui.views.inbox import MailItem, ReplyButton
from src.ui.widgets.status_bar import StatusBar

SHOTS = os.environ.get("CT_SCREENSHOTS")  # set to a directory to save SVG screenshots


def _shot(app, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(Path(SHOTS) / f"{name}.svg"))


async def _open(app, pilot, template_id):
    mail_list = app.screen.query_one("#mail-list", ListView)
    items = list(mail_list.query(MailItem))
    index = next(i for i, item in enumerate(items) if item.email.template_id == template_id)
    mail_list.focus()
    mail_list.index = index
    await pilot.pause()
    return items[index].email


def _cell(app, cell_id):
    return str(app.screen.query_one(cell_id, Static).render())


def test_reply_turn_and_fail_state_flow():
    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=1984)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, TerminalScreen)
            game = app.game
            status = app.screen.query_one(StatusBar)
            treasury0 = game.player.treasury

            # --- reply: confirm dialog, effects applied, UI updates instantly ---
            intel = await _open(app, pilot, "intel_001")
            assert len(app.screen.query(ReplyButton)) == 3
            _shot(app, "p2_1_intel_open")
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmReplyScreen)
            _shot(app, "p2_2_confirm")
            await pilot.press("y")
            await pilot.pause()
            assert isinstance(app.screen, TerminalScreen)
            assert intel.replied
            assert game.player.treasury == treasury0 - 40000
            assert status.treasury == game.player.treasury  # reactive var synced
            assert "▼40,000" in _cell(app, "#sb-treasury")  # delta flash
            assert len(app.screen.query(ReplyButton)) == 0  # can't answer twice
            _shot(app, "p2_3_replied")

            # Answering again is refused, no dialog.
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, TerminalScreen)

            # --- abort path leaves the email untouched ---
            await pilot.press("n")  # advance to week 2 (strike arrives)
            await pilot.pause()
            strike = await _open(app, pilot, "strike_001")
            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmReplyScreen)
            await pilot.press("n")  # abort
            await pilot.pause()
            assert strike.awaiting_response

            # --- advance week via hotkey: clock, status report, header ---
            assert game.clock.turn == 2
            assert status.turn == 2
            assert any(m.template_id == "status_report" for m in game.inbox.messages)
            assert "1984-01-09" in _cell(app, "#sb-date")

            # --- advance week via the sidebar button ---
            await pilot.click("#advance-week")
            await pilot.pause()
            assert game.clock.turn == 3
            _shot(app, "p2_4_week3")

            # --- reply button click path, with on-screen confirm ---
            strike = await _open(app, pilot, "strike_001")
            morale0 = game.player.morale
            await pilot.click(ReplyButton)
            await pilot.pause()
            await pilot.click("#confirm-yes")
            await pilot.pause()
            assert strike.replied and game.player.morale == morale0 + 3

            # --- event chain arrives 3 weeks after the reply (week 4) ---
            await pilot.press("n")
            await pilot.pause()
            chain = {"intel_002_success", "intel_002_burned"}
            assert chain & {m.template_id for m in game.inbox.messages}

            # --- fail state: revolution locks the terminal ---
            game.player.morale = 0
            app.state_changed()
            await pilot.press("n")
            await pilot.pause()
            assert game.game_over and game.game_over.cause == "revolution"
            from src.ui.screens.game_over import GameOverScreen

            assert isinstance(app.screen, GameOverScreen)  # non-dismissable lock: restart or exit
            assert "ARMED REVOLUTION" in app.screen.subject
            desktop = app.screen_stack[-2]
            first = desktop.query_one("#mail-list", ListView).query(MailItem).first()
            assert first.email.pinned and "SYSTEM PURGE" in first.email.subject
            assert desktop.query_one("#advance-week", Button).disabled
            assert "GOVERNMENT FALLEN" in str(desktop.query_one("#sb-title", Static).render())
            _shot(app, "p2_5_game_over")
            turn = game.clock.turn
            await pilot.press("n")  # every other command is locked out
            await pilot.pause()
            assert game.clock.turn == turn and isinstance(app.screen, GameOverScreen)

            # --- restart: a fresh campaign on a fresh desktop ---
            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.screen, TerminalScreen)
            assert app.game is not game and app.game.clock.turn == 1 and app.game.game_over is None
            await pilot.press("n")
            await pilot.pause()
            assert app.game.clock.turn == 2

    asyncio.run(run())


def test_hide_archived_toggle():
    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=5)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            mail_list = app.screen.query_one("#mail-list", ListView)
            await _open(app, pilot, "treasury_001")  # no options -> archived once read
            await _open(app, pilot, "brief_001")
            await pilot.press("h")
            await pilot.pause()
            shown = {item.email.template_id for item in mail_list.query(MailItem)}
            assert "treasury_001" not in shown and "brief_001" in shown  # the open one stays
            await pilot.press("h")
            await pilot.pause()
            assert len(mail_list.query(MailItem)) == 3

    asyncio.run(run())
