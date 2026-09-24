"""Phase 3: War Room map — grid model, overlay/stacking, fog of war, and the Textual widget."""

import asyncio
import os
from pathlib import Path

import pytest
from textual.widgets import OptionList, Static

from src.engine.data_loader import new_game
from src.engine.intel import unit_report
from src.engine.map_overlay import FRIENDLY, HOSTILE, MIXED, build_markers, marker_at
from src.engine.tick_engine import build_default_engine
from src.ui.app import CommandTerminalApp
from src.ui.widgets.map_canvas import MapCanvas

SHOTS = os.environ.get("CT_SCREENSHOTS")


@pytest.fixture
def game():
    return new_game(seed=1984)


# --- engine / model -------------------------------------------------------------


def test_world_map_grid_and_regions(game):
    world = game.world_map
    assert (world.width, world.height) == (128, 42)
    assert all(len(row) == world.width for row in world.base)
    assert world.region_at(69, 8).id == "frontier"
    assert world.region_at(14, 22).id == "aldmark"
    assert world.is_sea(0, 0)
    assert world.is_national_border(67, 10)  # Stonereach | Frontier boundary
    assert not world.is_national_border(30, 10)  # inside Harrowfen


def test_units_have_locations_on_land(game):
    units = game.all_units()
    assert len(game.player.units) == 7 and len(game.nations["vosk"].units) == 8
    for unit in units:
        assert not game.world_map.is_sea(unit.x, unit.y), unit.id


def test_markers_stack_same_cell(game):
    markers = build_markers(game)
    friendly_stack = marker_at(markers, 69, 21)
    assert friendly_stack.is_stack and friendly_stack.text == "[*]" and friendly_stack.side == FRIENDLY
    assert {u.id for u in friendly_stack.units} == {"kes_2_inf", "kes_aldmark_militia"}
    hostile_stack = marker_at(markers, 78, 20)
    assert hostile_stack.is_stack and hostile_stack.side == HOSTILE
    single = marker_at(markers, 69, 8)
    assert single.text == "[X]" and not single.is_stack


def test_overlapping_symbols_merge_and_mixed_sides(game):
    game.unit("kes_1_inf").location = (77, 7)  # symbol 76-78 would overlap V-4R at 77-79
    markers = build_markers(game)
    merged = marker_at(markers, 78, 7) or marker_at(markers, 77, 7)
    assert merged.is_stack and merged.side == MIXED
    # No two markers ever share a cell.
    cells = [(m.y, x) for m in markers for x in range(m.x - 1, m.x + 2)]
    assert len(cells) == len(set(cells))


def test_enemy_intel_is_estimated_cached_and_leaves_main_rng_alone(game):
    enemy = game.unit("vosk_4_rifle")
    rng_state = game.rng.getstate()
    first = unit_report(game, enemy)
    assert unit_report(game, enemy) is first  # cached for the week
    assert game.rng.getstate() == rng_state  # looking at the map changes nothing
    assert first.strength.low <= first.strength.high
    engine = build_default_engine(game)
    engine.advance()
    second = unit_report(game, enemy)
    assert second.turn == 2 and second is not first


def test_intel_is_deterministic_per_campaign_seed():
    a, b = new_game(seed=42), new_game(seed=42)
    for unit_id in ("vosk_4_rifle", "vosk_1_gtank", "vosk_3_gtank"):
        ra, rb = unit_report(a, a.unit(unit_id)), unit_report(b, b.unit(unit_id))
        assert (ra.strength, ra.reported_type, ra.identified) == (rb.strength, rb.reported_type, rb.identified)


# --- UI -------------------------------------------------------------------------


def _strip_text(canvas, map_y):
    return "".join(seg.text for seg in canvas.render_line(map_y - int(canvas.scroll_offset.y)))


def _readout(app):
    return str(app.screen.query_one("#intel-readout", Static).render())


def test_war_room_renders_overlay_and_readouts():
    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=1984)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            canvas = app.screen.query_one(MapCanvas)
            assert canvas.has_focus

            # Layout: map pane ~70% of the main view, and the map is wider than the pane (pannable).
            pane = app.screen.query_one("#map-pane")
            intel = app.screen.query_one("#intel-pane")
            assert 0.62 < pane.size.width / (pane.size.width + intel.size.width) < 0.78
            assert canvas.virtual_size.width > canvas.size.width
            assert canvas.size.height >= 30  # map fills the pane vertically

            # Overlay drawn at exact columns (row 8: K-01 [X] centred on x=69).
            canvas.jump_to(69, 8)
            await pilot.pause()
            line = _strip_text(canvas, 8)
            sx = int(canvas.scroll_offset.x)
            assert line[68 - sx:71 - sx] == "[X]"
            assert len(line) == canvas.size.width  # nothing overflows the pane
            if SHOTS:
                Path(SHOTS).mkdir(parents=True, exist_ok=True)
                app.save_screenshot(str(Path(SHOTS) / "p3_1_friendly.svg"))

            text = _readout(app)
            assert "FRIENDLY FORMATION" in text and "9,400 / 10,000" in text and "SUPPLY" in text

            # Hostile: estimates only; true strength and morale hidden.
            canvas.jump_to(78, 7)
            await pilot.pause()
            text = _readout(app)
            assert "HOSTILE" in text and "EST. STRENGTH" in text and "CERTAINTY" in text
            assert "10,500" not in text and "UNKNOWN" in text
            if SHOTS:
                app.save_screenshot(str(Path(SHOTS) / "p3_2_hostile.svg"))

            # Stack readout lists both formations.
            canvas.jump_to(69, 21)
            await pilot.pause()
            text = _readout(app)
            assert "STACK — 2 FORMATIONS" in text and "MILITIA" in text
            if SHOTS:
                app.save_screenshot(str(Path(SHOTS) / "p3_3_stack.svg"))

            # Keyboard: cycle to the next unit.
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert marker_at(canvas.markers, *canvas.cursor) is not None
            assert canvas.cursor != (69, 21)

            # Panning: cursor to the far east scrolls the map.
            canvas.jump_to(120, 30)
            await pilot.pause()
            assert canvas.scroll_offset.x > 0
            assert "TAL VAROS" in _readout(app)
            if SHOTS:
                app.save_screenshot(str(Path(SHOTS) / "p3_4_panned.svg"))

            # Mouse: click a cell -> cursor goes there (content offset + scroll).
            canvas.scroll_to(0, 0, animate=False, immediate=True)
            await pilot.pause()
            await pilot.click(MapCanvas, offset=(69, 15))
            await pilot.pause()
            assert canvas.cursor == (69, 15)
            assert "K-02" in _readout(app)

            # Order-of-battle list: highlighting a hostile contact jumps the cursor to it.
            orbat = app.screen.query_one("#orbat-list", OptionList)
            orbat.focus()
            orbat.highlighted = orbat.get_option_index("vosk_3_gtank")
            await pilot.pause()
            assert canvas.cursor == (101, 27)

            # Advancing the week keeps the War Room consistent (intel re-rolled, no crash).
            await pilot.press("n")
            await pilot.pause()
            assert unit_report(app.game, app.game.unit("vosk_3_gtank")).turn == 2

    asyncio.run(run())


@pytest.mark.parametrize("size", [(100, 30), (80, 24)])
def test_war_room_small_terminal_does_not_break(size):
    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=3)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            canvas = app.screen.query_one(MapCanvas)
            for _ in range(40):
                await pilot.press("right_square_bracket")
            await pilot.pause()
            for y in range(canvas.size.height):
                strip = canvas.render_line(y)
                assert strip.cell_length == canvas.size.width

    asyncio.run(run())


def test_identified_contacts_are_never_misclassified():
    for seed in range(60):
        state = new_game(seed=seed)
        for unit in state.nations["vosk"].units:
            report = unit_report(state, unit)
            if report.identified:
                assert report.reported_type == unit.unit_type and not report.misidentified
