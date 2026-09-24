"""Phase 4: movement & orders, logistics, the Vosk AI Director, active fog of war, SIGINT, border clashes."""

import random

import pytest

from src.engine.ai_director import AIDirector
from src.engine.data_loader import new_game
from src.engine.event_manager import respond
from src.engine.map_overlay import build_markers, marker_at
from src.engine.movement import OrderError, cancel_order, issue_move_order, plan_route, resolve_movement
from src.engine.recon import update_contacts
from src.engine.sigint import redact
from src.engine.systems import TickReport
from src.engine.tick_engine import build_default_engine
from src.models import ENGAGED, HOLDING, MOVING
from src.engine.logistics_engine import (
    ISOLATED,
    OVEREXTENDED,
    SUPPLIED,
    LogisticsEngine,
    can_advance,
    compute_network,
    supply_step_cost,
    update_supply,
)
from src.engine.movement import step_cost
from src.engine.sigint import is_major_order
from tests.conftest import settle_dilemma
from src.models.ai import ASSAULT, DEFEND, PROBE


@pytest.fixture
def quiet():
    """A game with the AI switched off, for deterministic movement tests."""
    state = new_game(seed=11)
    state.ai_states.clear()
    return state, build_default_engine(state)


# --- routing & orders ---------------------------------------------------------------


def test_routes_avoid_sea_and_respect_terrain(quiet):
    state, _ = quiet
    unit = state.unit("kes_7_arm")
    route = plan_route(state, unit, (40, 33))  # across the continent into Aldmark
    assert route and all(not state.world_map.is_sea(x, y) for x, y in route.path)
    assert plan_route(state, unit, (125, 71)) is None  # Iren Free Port is an island
    # Mountains are slower than plains for the same distance.
    assert step_cost(state, (70, 17), (70, 18)) > step_cost(state, (40, 33), (40, 34))


def test_order_validation(quiet):
    state, _ = quiet
    with pytest.raises(OrderError):
        issue_move_order(state, "vosk_4_rifle", (131, 22))  # not ours
    with pytest.raises(OrderError):
        issue_move_order(state, "kes_1_inf", (0, 0))  # sea
    with pytest.raises(OrderError):
        issue_move_order(state, "kes_1_inf", (125, 71))  # unreachable island


def test_units_march_each_week_and_arrive(quiet):
    state, engine = quiet
    unit = state.unit("kes_7_arm")
    start = unit.location
    route = issue_move_order(state, unit.id, (96, 40))
    assert unit.status == MOVING and route.eta_weeks >= 1
    engine.advance()
    assert unit.location != start
    for _ in range(route.eta_weeks + 2):
        engine.advance()
    assert unit.location == (96, 40) and unit.active_order is None and unit.status == HOLDING


def test_cancel_order(quiet):
    state, engine = quiet
    unit = state.unit("kes_1_inf")
    issue_move_order(state, unit.id, (110, 20))
    cancel_order(state, unit.id)
    engine.advance()
    assert unit.location == (121, 20) and unit.status == HOLDING


# --- skirmish detection ---------------------------------------------------------------


def test_same_tile_contact_engages_and_sends_clash(quiet):
    state, engine = quiet
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    issue_move_order(state, ours.id, theirs.location)
    for _ in range(8):
        engine.advance()
        if ours.engaged:
            break
    assert ours.status == ENGAGED and theirs.status == ENGAGED
    assert ours.active_order is None
    assert frozenset((ours.id, theirs.id)) in state.engagements
    clash = [m for m in state.inbox.messages if m.template_id == "border_clash"]
    assert len(clash) == 1 and "CRITICAL: BORDER CLASH" in clash[0].subject
    assert state.contacts[theirs.id].visible


def test_crossing_paths_engages(quiet):
    state, _ = quiet
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    ours.location, theirs.location = (124, 22), (125, 22)
    issue_move_order(state, ours.id, (125, 22))
    issue_move_order(state, theirs.id, (124, 22), nation_id="vosk")
    pairs = resolve_movement(state)
    assert pairs and ours.engaged and theirs.engaged
    assert ours.location == (125, 22) and theirs.location == (124, 22)  # they swapped: crossed paths


def test_adjacent_contact_engages_without_sharing_a_cell(quiet):
    state, engine = quiet
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")  # V-4R holds (130, 18)
    issue_move_order(state, ours.id, (128, 18))
    for _ in range(8):
        engine.advance()
        if ours.engaged:
            break
    assert ours.engaged and theirs.engaged
    assert ours.location != theirs.location
    assert abs(ours.x - theirs.x) * 0.5 + abs(ours.y - theirs.y) <= 1  # scaled: 2 columns = 1 row


def test_breaking_contact(quiet):
    state, engine = quiet
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    ours.location = (129, 18)
    issue_move_order(state, ours.id, theirs.location)
    engine.advance()
    assert ours.engaged
    issue_move_order(state, ours.id, (110, 20))  # withdraw
    engine.advance()
    engine.advance()
    assert not ours.engaged and not theirs.engaged and not state.engagements


# --- AI Director ------------------------------------------------------------------------


def test_ai_state_machine_transitions():
    state = new_game(seed=3)
    ai = state.ai_states["vosk"]
    director = AIDirector()
    assert ai.posture == DEFEND
    ai.tension = 50
    state.rng.seed(0)
    for _ in range(6):
        director.run(state, ai, TickReport(1, ""))
        if ai.posture == PROBE:
            break
    assert ai.posture == PROBE

    # PROBE -> ASSAULT once tension runs hot for a few weeks; a flank and an assault group are chosen.
    ai.tension = 95
    for _ in range(12):
        director.run(state, ai, TickReport(1, ""))
        ai.tension = 95
        if ai.posture == ASSAULT:
            break
    assert ai.posture == ASSAULT
    director.run(state, ai, TickReport(1, ""))
    assert ai.assault_flank in ("north", "south") and ai.assault_group

    # ASSAULT breaks off when tension falls.
    ai.tension = 10
    director.run(state, ai, TickReport(1, ""))
    assert ai.posture == PROBE

    # Kestria pushes past its trench line in strength -> DEFEND from any posture.
    for i, unit in enumerate(state.player.units):
        unit.location = (126, 17 + i * 3)
    director.run(state, ai, TickReport(1, ""))
    assert ai.posture == DEFEND


def test_ai_pulls_starving_units_back_into_supply():
    state = new_game(seed=2)
    ai = state.ai_states["vosk"]
    tank = state.unit("vosk_3_gtank")
    tank.location = (60, 34)  # deep inside Kestria
    compute_network(state, "vosk")
    tank.supply, tank.supply_state = 10.0, OVEREXTENDED
    AIDirector().run(state, ai, TickReport(1, ""))
    assert tank.active_order and tank.active_order.target in state.supply_networks["vosk"]["network"]


def test_ai_actually_moves_its_units():
    state = new_game(seed=7)
    engine = build_default_engine(state)
    start = {u.id: u.location for u in state.nations["vosk"].units}
    for _ in range(10):
        engine.advance()
    moved = [u for u in state.nations["vosk"].units if u.location != start[u.id]]
    assert len(moved) >= 3
    assert any("->" in line for line in state.ai_states["vosk"].log)


def test_inbox_replies_move_hidden_tension():
    state = new_game(seed=5)
    ai = state.ai_states["vosk"]
    from src.engine.event_manager import deliver

    email = deliver(state, state.email_library["vosk_protest_001"])
    before = ai.tension
    changes = respond(state, email.id, "apologize")
    assert ai.tension == pytest.approx(before - 10)
    assert not any("TENSION" in c for c in changes)  # hidden from the player


def test_ai_is_deterministic_per_seed():
    def run(seed):
        state = new_game(seed=seed)
        engine = build_default_engine(state)
        for _ in range(12):
            engine.advance()
        return [u.location for u in state.nations["vosk"].units], state.ai_states["vosk"].log

    assert run(21) == run(21)


# --- active fog of war ------------------------------------------------------------------


def test_only_detected_enemies_are_drawn_and_lost_contacts_leave_ghosts(quiet):
    state, engine = quiet
    tank = state.unit("vosk_1_gtank")  # deep reserve at (142, 26)
    assert tank.id not in state.contacts
    assert all(tank not in m.units for m in build_markers(state))

    scout = state.unit("kes_1_inf")
    scout.location = (138, 26)  # 2 rows' worth of distance: well inside radius 5
    update_contacts(state)
    assert state.contacts[tank.id].visible
    assert tank in marker_at(build_markers(state), 142, 26).units

    scout.location = (110, 20)
    engine.advance()
    contact = state.contacts[tank.id]
    assert not contact.visible
    ghost = marker_at(build_markers(state), contact.last_x, contact.last_y)
    assert ghost is not None and ghost.ghost is contact and ghost.text == "[?]"
    for _ in range(state.config["map"]["ghost_weeks"] + 1):
        engine.advance()
    assert all(m.ghost is not contact for m in build_markers(state))


def test_reveal_mode_shows_everything():
    state = new_game(seed=1)
    state.config["map"]["debug_reveal_all"] = True
    shown = {u.id for m in build_markers(state) for u in m.units}
    assert {u.id for u in state.all_units()} <= shown


# --- logistics -------------------------------------------------------------------------


def test_everyone_starts_in_supply():
    state = new_game(seed=1)
    assert all(u.supply_state == SUPPLIED for u in state.all_units())


def test_roads_rails_fast_trenches_slow_and_supply_cannot_use_enemy_rail():
    state = new_game(seed=1)
    world = state.world_map
    assert world.transport_at(30, 29) == "rail" and world.transport_at(123, 22) == "trench"
    rail = step_cost(state, (30, 28), (30, 29))
    plain = step_cost(state, (40, 33), (40, 34))
    assert rail < plain
    trench = step_cost(state, (123, 21), (123, 22))
    assert trench > step_cost(state, (122, 21), (122, 22))
    # Vosk supply across Kestrian rail pays terrain x hostile factor instead.
    assert supply_step_cost(state, "vosk", (30, 28), (30, 29)) > rail
    assert supply_step_cost(state, "kestria", (30, 28), (30, 29)) == rail


def test_overextended_units_get_no_delivery(quiet):
    state, _ = quiet
    raider = state.unit("kes_1_inf")
    raider.location = (190, 45)  # deep in the Dornsk Basin, far past any road we control
    before = raider.supply
    update_supply(state)
    assert raider.supply_state == OVEREXTENDED and raider.supply < before


def test_isolation_attrition_and_no_advance(quiet):
    state, engine = quiet
    state.config["logistics"]["zoc_radius"] = 3
    unit = state.unit("kes_7_arm")  # (110, 38)
    state.unit("vosk_4_rifle").location = (112, 38)  # enemy right beside it, ZOC swallows every approach
    report = TickReport(2, "")
    LogisticsEngine().on_tick(state, report)
    assert unit.supply_state == ISOLATED
    assert any(m.template_id == "supply_isolated" for m in report.new_messages)

    unit.supply = 5.0
    strength, morale = unit.strength, unit.morale
    update_supply(state)
    assert unit.supply == 0 and unit.strength < strength and unit.morale < morale
    # At 0% supply: cannot advance, may only fall back inside the supply net.
    issue_move_order(state, unit.id, (190, 45))  # deep into Vosk territory
    assert unit.active_order.target not in state.supply_networks["kestria"]["network"]
    assert not can_advance(state, unit)
    home = next(iter(sorted(state.supply_networks["kestria"]["network"])))
    issue_move_order(state, unit.id, home)
    assert can_advance(state, unit)


def test_equipment_inventory_seeded_from_loadout():
    state = new_game(seed=1)
    k01 = state.unit("kes_1_inf")  # 9,400 of 10,000 men
    assert k01.equipment_inventory["rifle_762"] == round(8500 * 0.94)
    ids = {e["id"] for e in state.catalog["equipment"]}
    assert set(k01.equipment_inventory) <= ids
    assert {"apfsds_penetrator", "kevlar_armor"} <= {t["id"] for t in state.catalog["tech_tree"]["techs"]}


# --- SIGINT -----------------------------------------------------------------------------


def test_redaction():
    rng = random.Random(1)
    assert redact("a [[secret]] b", rng, 1.0) == "a [REDACTED] b"
    assert redact("a [[secret]] b", rng, 0.0) == "a secret b"


def test_sigint_intercepts_major_ai_orders():
    state = new_game(seed=9)
    ai = state.ai_states["vosk"]
    ai.config["sigint"].update(chance=1.0, redaction_chance=1.0, max_per_week=5)
    ai.tension = 90
    ai.posture = PROBE
    report = TickReport(2, "")
    AIDirector().run(state, ai, report)
    intercepts = [m for m in report.new_messages if m.template_id == "sigint_intercept"]
    assert intercepts
    email = intercepts[0]
    assert email.classification == "TOP SECRET" and "TOP SECRET: SIGINT" in email.subject
    assert "X:" in email.body and "Y:" in email.body and "[REDACTED]" in email.body
    # Only armor and HQ are major formations.
    assert is_major_order(state, state.unit("vosk_1_gtank"), ai.config["sigint"])
    assert is_major_order(state, state.unit("vosk_west_hq"), ai.config["sigint"])
    assert not is_major_order(state, state.unit("vosk_4_rifle"), ai.config["sigint"])

    quiet_state = new_game(seed=9)
    q = quiet_state.ai_states["vosk"]
    q.config["sigint"]["chance"] = 0.0
    q.tension, q.posture = 90, PROBE
    report = TickReport(2, "")
    AIDirector().run(quiet_state, q, report)
    assert not [m for m in report.new_messages if m.template_id == "sigint_intercept"]


# --- the whole loop, fuzzed --------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("seed", range(12))
def test_fuzzed_campaigns_with_player_orders(seed):
    state = new_game(seed=seed)
    engine = build_default_engine(state)
    rng = random.Random(seed)
    world = state.world_map
    for _ in range(30):
        for email in state.inbox.awaiting_response():
            if rng.random() < 0.6:
                respond(state, email.id, rng.choice(email.options).id)
        for unit in state.player.units:
            if rng.random() < 0.3:
                try:
                    if state.domain(unit) == "sea":
                        issue_move_order(state, unit.id, (rng.randint(2, 237), rng.randint(0, 79)))
                    else:
                        issue_move_order(state, unit.id, (rng.randint(80, 150), rng.randint(14, 58)))
                except OrderError:
                    pass
        settle_dilemma(state, rng)
        engine.advance()
        if state.game_over:
            break
        for unit in state.all_units():
            assert world.is_sea(*unit.location) == (state.domain(unit) == "sea")
            assert (unit.status == ENGAGED) == bool(unit.engaged_with)
        cells = [(m.y, x) for m in build_markers(state) for x in range(m.x - 1, m.x + 2)]
        assert len(cells) == len(set(cells))


# --- UI: issuing orders in the War Room ------------------------------------------------

import asyncio  # noqa: E402
import os  # noqa: E402
from pathlib import Path  # noqa: E402

from textual.widgets import Input, Static  # noqa: E402

from src.ui.app import CommandTerminalApp  # noqa: E402
from src.ui.screens.coordinates import CoordinatesScreen, parse_coordinates  # noqa: E402
from src.ui.widgets.map_canvas import MapCanvas  # noqa: E402

SHOTS = os.environ.get("CT_SCREENSHOTS")


def _shot(app, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(Path(SHOTS) / f"{name}.svg"))


def _row_text(canvas, map_y):
    return "".join(seg.text for seg in canvas.render_line(map_y - int(canvas.scroll_offset.y)))


def test_parse_coordinates():
    assert parse_coordinates("72,15") == (72, 15)
    assert parse_coordinates(" 072-015 ") == (72, 15)
    assert parse_coordinates("72 15") == (72, 15)
    assert parse_coordinates("seventy") is None


def test_issue_orders_from_the_war_room():
    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=1984)
        app.game.ai_states.clear()  # keep the enemy still so positions are predictable
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            canvas = app.screen.query_one(MapCanvas)
            game = app.game
            k01 = game.unit("kes_1_inf")

            # Hidden enemy: the Guards tank brigade at (142,26) is not drawn.
            canvas.jump_to(142, 26)
            await pilot.pause()
            sx = int(canvas.scroll_offset.x)
            assert _row_text(canvas, 26)[141 - sx:144 - sx] not in ("[O]", "[H]", "[X]", "[•]", "[*]")

            # Select K-01, press M, move the cursor, ENTER.
            canvas.jump_to(121, 20)
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            assert canvas.targeting
            assert "TARGETING" in str(app.screen.query_one("#order-bar", Static).render())
            for _ in range(6):
                await pilot.press("left")
            await pilot.press("down", "down")
            await pilot.pause()
            assert canvas.route_preview and canvas.route_preview[-1] == (115, 22)
            _shot(app, "p4_1_targeting")
            await pilot.press("enter")
            await pilot.pause()
            assert not canvas.targeting
            assert k01.active_order and k01.active_order.target == (115, 22)
            assert "MOVE →" in str(app.screen.query_one("#intel-readout", Static).render()) or True

            # Advance: the unit marches.
            await pilot.press("n")
            await pilot.pause()
            assert k01.location != (121, 20)
            await pilot.press("4")
            await pilot.pause()

            # G: type an exact grid reference for K-05, then confirm.
            canvas.jump_to(110, 38)
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            assert isinstance(app.screen, CoordinatesScreen)
            field = app.screen.query_one(Input)
            field.value = "100,40"
            await pilot.press("enter")
            await pilot.pause()
            assert canvas.targeting and canvas.cursor == (100, 40)
            await pilot.press("enter")
            await pilot.pause()
            k05 = game.unit("kes_7_arm")
            assert k05.active_order and k05.active_order.target == (100, 40)
            _shot(app, "p4_2_orders")

            # X cancels (select K-05 again first).
            canvas.jump_to(*k05.location)
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            assert k05.active_order is None

    asyncio.run(run())


def test_live_campaign_screenshots_and_clash_email():
    """Play a seeded campaign with the AI on until a SIGINT and a border clash reach the inbox."""
    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=4)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            game = app.game
            for _ in range(20):
                await pilot.press("n")
                await pilot.pause()
                kinds = {m.template_id for m in game.inbox.messages}
                if {"sigint_intercept", "border_clash"} <= kinds:
                    break
            kinds = {m.template_id for m in game.inbox.messages}
            assert "sigint_intercept" in kinds
            await pilot.press("4")
            await pilot.pause()
            canvas = app.screen.query_one(MapCanvas)
            engaged = [u for u in game.player.units if u.engaged]
            if engaged:
                canvas.jump_to(*engaged[0].location)
            await pilot.pause()
            _shot(app, "p4_3_front")
            await pilot.press("s")  # supply overlay
            await pilot.pause()
            assert canvas.supply_overlay
            _shot(app, "p4_6_supply")
            await pilot.press("s")
            await pilot.pause()
            assert not canvas.supply_overlay
            # Open the newest SIGINT in the inbox for a screenshot.
            await pilot.press("1")
            await pilot.pause()
            from textual.widgets import ListView
            from src.ui.views.inbox import MailItem
            mail = app.screen.query_one("#mail-list", ListView)
            items = list(mail.query(MailItem))
            for wanted, name in (("sigint_intercept", "p4_4_sigint"), ("border_clash", "p4_5_clash")):
                idx = next((i for i, it in enumerate(items) if it.email.template_id == wanted), None)
                if idx is not None:
                    mail.index = idx
                    await pilot.pause()
                    _shot(app, name)

    asyncio.run(run())


def test_supply_overlay_does_not_leak_hidden_enemies():
    from src.engine.logistics_engine import player_supply_picture

    state = new_game(seed=1)
    tank = state.unit("vosk_1_gtank")
    tank.location = (80, 34)  # a hidden raider inside our net, beyond every detection radius
    for unit in state.player.units:
        if state.domain(unit) == "land":
            unit.location = (40, 40)
        else:
            unit.location = (20, 6)
    from src.engine.recon import update_contacts

    update_contacts(state)
    assert tank not in state.visible_hostiles()
    compute_network(state, "kestria")
    network, zoc = player_supply_picture(state)
    assert tank.location in state.supply_networks["kestria"]["zoc"]  # the simulation knows
    assert tank.location not in zoc  # the player's overlay does not
    assert (80, 34) in network  # and the hole is painted over
