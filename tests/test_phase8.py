"""Phase 8: chain of command & insubordination, electronic warfare, scorched earth & engineers, victory,
and save / load."""

import asyncio
import json

import pytest

from src.engine.combat_engine import StanceError, _battle_for, defense, fight_round, set_stance
from src.engine.command import CommandError, CommandSystem, fire_support, relieve_commander
from src.engine.data_loader import new_game
from src.engine.electronic_warfare import EWSystem, ai_jamming, is_dark, jam
from src.engine.engineering import EngineeringSystem, damaged_cells, sabotage
from src.engine.event_manager import GameOverError
from src.engine.fail_states import FailStateSystem, enemy_capital
from src.engine.map_overlay import build_markers, marker_at
from src.engine.movement import OrderError, _engage, issue_move_order, plan_route, step_cost
from src.engine.recon import update_contacts
from src.engine.savegame import SaveError, load_game, save_game, snapshot
from src.engine.systems import TickReport
from src.engine.tick_engine import build_default_engine
from src.models import ASSAULT, DEFEND, WITHDRAW
from src.models.ai import ASSAULT as AI_ASSAULT
from tests.conftest import settle_dilemma


@pytest.fixture
def quiet():
    state = new_game(seed=88)
    state.ai_states.clear()
    return state, build_default_engine(state)


def traits(state):
    return state.catalog["commanders"]["traits"]


def insubordination(report):
    return [m for m in report.new_messages if m.template_id == "command_insubordination"]


# --- 1. chain of command ------------------------------------------------------------------------


def test_every_commander_has_hidden_traits(quiet):
    state, _ = quiet
    assert all(u.traits for u in state.all_units())
    hale = state.unit("kes_1_inf")
    assert hale.commander == "Maj. Gen. Oskar Hale" and hale.traits == ["cautious"] and not hale.traits_known


def test_cautious_general_refuses_an_unsupported_assault(quiet):
    state, _ = quiet
    traits(state)["cautious"]["refuse_unsupported_assault"] = 1.0
    k01 = state.unit("kes_1_inf")
    assert not fire_support(state, k01)  # no guns, ships or aircraft anywhere near
    set_stance(state, k01.id, ASSAULT)
    issue_move_order(state, k01.id, (126, 20))
    assert k01.pending_orders == ["stance", "move"]
    report = TickReport(2, "")
    CommandSystem().on_tick(state, report)
    assert k01.stance == DEFEND and k01.active_order is None
    assert k01.traits_known  # the refusal tells us who he is
    mail = insubordination(report)
    assert mail and "COMMAND INSUBORDINATION" in mail[0].subject
    assert "refuses to advance without heavy fire support" in mail[0].body


def test_fire_support_makes_the_cautious_general_attack(quiet):
    state, _ = quiet
    traits(state)["cautious"]["refuse_unsupported_assault"] = 1.0
    k01 = state.unit("kes_1_inf")
    state.unit("kes_12_art").location = (118, 21)  # guns 1-2 cells behind him
    assert fire_support(state, k01)
    set_stance(state, k01.id, ASSAULT)
    report = TickReport(2, "")
    CommandSystem().on_tick(state, report)
    assert k01.stance == ASSAULT and not insubordination(report)
    # Air cover works too.
    state.unit("kes_12_art").location = (104, 36)
    state.air_wings[0].sector = "frontier"
    assert "air cover" in fire_support(state, k01)


def test_glory_hound_will_not_retreat_and_low_morale_armies_balk(quiet):
    state, _ = quiet
    traits(state)["glory_hound"]["refuse_withdraw"] = 1.0
    k08 = state.unit("kes_5_inf")  # Maj. Gen. Rehn, a glory-hound
    set_stance(state, k08.id, WITHDRAW)
    report = TickReport(2, "")
    CommandSystem().on_tick(state, report)
    assert k08.stance == DEFEND and "refuses to give ground" in insubordination(report)[0].body
    # Even a steady officer balks at a suicidal attack when the army's loyalty is gone.
    state.catalog["commanders"]["low_morale_refusal"] = 1.0
    state.player.military_morale = 20
    k03 = state.unit("kes_2_inf")
    set_stance(state, k03.id, ASSAULT)
    CommandSystem().on_tick(state, TickReport(3, ""))
    assert k03.stance == DEFEND


def test_relieving_a_general_costs_military_morale(quiet):
    state, _ = quiet
    k01 = state.unit("kes_1_inf")
    k01.traits_known = True
    mil, morale = state.player.military_morale, k01.morale
    old, new = relieve_commander(state, k01.id)
    assert old == "Maj. Gen. Oskar Hale" and new != old and k01.commander == new
    assert state.player.military_morale == pytest.approx(mil - 6)
    assert k01.morale == pytest.approx(morale - 8)
    assert k01.traits and not k01.traits_known  # the new man is an unknown quantity
    assert any(m.template_id == "command_change" for m in state.inbox.messages)
    with pytest.raises(CommandError):
        relieve_commander(state, "vosk_4_rifle")


def test_traits_shape_combat(quiet):
    state, _ = quiet
    k01 = state.unit("kes_1_inf")
    k01.stance = DEFEND
    cautious = defense(state, k01)
    k01.traits = ["steady"]
    assert cautious == pytest.approx(defense(state, k01) * 1.1)


# --- 2. electronic warfare ---------------------------------------------------------------------------


def test_jammed_formations_go_dark(quiet):
    state, engine = quiet
    state.config["electronic_warfare"]["random_chance"] = 0.0  # no natural interference in this test
    k01 = state.unit("kes_1_inf")
    zone, lost = jam(state, k01.location, 3, 2)
    assert k01 in lost and is_dark(state, k01)
    assert not is_dark(state, state.unit("kes_2_inf"))  # 18 rows away: still in contact
    with pytest.raises(OrderError):
        issue_move_order(state, k01.id, (110, 20))
    with pytest.raises(StanceError):
        set_stance(state, k01.id, ASSAULT)
    with pytest.raises(CommandError):
        relieve_commander(state, k01.id)
    markers = build_markers(state)
    lost_marker = marker_at(markers, 121, 20)
    assert lost_marker.lost is k01 and lost_marker.text == "[?]" and not lost_marker.units
    # It still follows its last orders and fights; after two weeks the signal returns.
    report = TickReport(2, "")
    EWSystem().on_tick(state, report)
    assert is_dark(state, k01)
    EWSystem().on_tick(state, report)
    assert not is_dark(state, k01) and not state.jammed
    assert any(m.template_id == "signal_restored" for m in report.new_messages)


def test_dark_formations_stop_spotting(quiet):
    state, _ = quiet
    tank = state.unit("vosk_1_gtank")
    scout = state.unit("kes_1_inf")
    scout.location = (138, 26)
    update_contacts(state)
    assert state.contacts[tank.id].visible
    jam(state, scout.location, 2, 1)
    update_contacts(state)
    assert not state.contacts[tank.id].visible  # his reports cannot get through


def test_vosk_jams_the_heaviest_concentration():
    state = new_game(seed=4)
    ai = state.ai_states["vosk"]
    ai.posture = AI_ASSAULT
    ai.config["ew"]["chance"]["ASSAULT"] = 1.0
    report = TickReport(2, "")
    zone = ai_jamming(state, ai, report)
    assert zone in state.jammed
    assert any(m.template_id == "signal_lost" for m in report.new_messages)
    assert ai_jamming(state, ai, report) is None  # one zone at a time


# --- 3. scorched earth & engineers ----------------------------------------------------------------------


def test_sabotage_wrecks_rail_and_removes_its_bonus(quiet):
    state, _ = quiet
    world = state.world_map
    cell = (110, 36)  # the Eastmarch trunk line near Kestrel Cross
    assert world.transport_at(*cell) == "rail"
    fast = step_cost(state, (109, 36), cell)
    hq = state.unit("kes_frontier_hq")
    before = plan_route(state, hq, (120, 36)).cost
    destroyed = sabotage(state, hq, cell, 1.0, TickReport(2, ""))
    assert 3 <= len(destroyed) <= 6 and cell in destroyed
    assert world.transport_at(*cell) == "destroyed_rail" and state.map_damage[cell] == "destroyed_rail"
    assert step_cost(state, (109, 36), cell) > fast  # no bonus on wrecked track
    assert plan_route(state, hq, (120, 36)).cost > before  # cached routes were dropped


def test_routing_units_blow_the_line_behind_them(quiet):
    state, _ = quiet
    state.config["scorched_earth"]["rout_chance"] = 1.0
    ours, theirs = state.unit("kes_2_inf"), state.unit("vosk_5_rifle")
    ours.location, theirs.location = (121, 36), (123, 36)
    ours.morale = 15.2  # one week of fire breaks them
    _engage(state, ours, theirs, [])
    report = TickReport(2, "")
    fight_round(state, _battle_for(state, {ours.id, theirs.id}), {ours.id, theirs.id}, report)
    assert ours.routing and damaged_cells(state)
    assert any(m.template_id == "scorched_earth" for m in report.new_messages)


def test_engineers_rebuild_wrecked_track(quiet):
    state, _ = quiet
    eng = state.unit("kes_1_eng")
    assert state.role(eng) == "engineer"
    rubble = set(damaged_cells(state))  # the rail bed across no-man's-land, far from the engineers
    destroyed = sabotage(state, state.unit("kes_frontier_hq"), (106, 36), 1.0)
    report = TickReport(2, "")
    EngineeringSystem().on_tick(state, report)
    assert len(set(damaged_cells(state)) - rubble) == len(destroyed) - 2  # two sections a week
    for _ in range(3):
        EngineeringSystem().on_tick(state, report)
    assert set(damaged_cells(state)) == rubble and not state.map_damage
    assert state.world_map.transport_at(106, 36) == state.world_map.base_transport[(106, 36)]


def test_engineers_can_rebuild_the_rail_across_no_mans_land(quiet):
    state, _ = quiet
    eng = state.unit("kes_1_eng")
    eng.location = (122, 36)
    assert state.world_map.transport_at(123, 36) == "destroyed_rail"
    EngineeringSystem().on_tick(state, TickReport(2, ""))
    assert state.world_map.transport_at(123, 36) == "rail" and state.map_damage[(123, 36)] == "rail"


# --- 4. victory -----------------------------------------------------------------------------------------


def _clear_capital(state):
    name, cell = enemy_capital(state)
    for unit in state.nations["vosk"].units:
        if abs(unit.x - cell[0]) * 0.5 + abs(unit.y - cell[1]) <= 3:
            unit.location = (150, 46)
    return name, cell


def test_holding_the_enemy_capital_for_two_weeks_wins_the_war(quiet):
    state, engine = quiet
    name, cell = _clear_capital(state)
    assert name == "Karzan"
    state.unit("kes_7_arm").location = cell
    report = TickReport(2, "")
    FailStateSystem().on_tick(state, report)
    assert state.occupation_weeks == 1 and state.game_over is None
    assert any(m.template_id == "capital_entered" for m in report.new_messages)
    FailStateSystem().on_tick(state, report)
    assert state.game_over is not None and state.game_over.cause == "victory"
    victory = [m for m in report.new_messages if m.template_id == "victory"]
    assert victory and victory[0].pinned and "UNCONDITIONAL SURRENDER" in victory[0].body
    with pytest.raises(GameOverError):
        engine.advance()


def test_counter_attack_resets_the_occupation(quiet):
    state, _ = quiet
    _, cell = _clear_capital(state)
    state.unit("kes_7_arm").location = cell
    FailStateSystem().on_tick(state, TickReport(2, ""))
    state.unit("vosk_1_gtank").location = (cell[0] + 1, cell[1])  # they fight back into the city
    FailStateSystem().on_tick(state, TickReport(3, ""))
    assert state.occupation_weeks == 0 and state.game_over is None


def test_enemy_economic_and_military_collapse_wins_the_war(quiet):
    state, _ = quiet
    vosk = state.nations["vosk"]
    vosk.treasury, vosk.military_morale = -10, 0
    FailStateSystem().on_tick(state, TickReport(2, ""))
    assert state.game_over.cause == "victory"


def test_enemy_military_morale_falls_with_defeats(quiet):
    state, engine = quiet
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    theirs.location = (123, 20)
    theirs.strength = 3
    _engage(state, ours, theirs, [])
    before = state.nations["vosk"].military_morale
    from src.engine.combat_engine import CombatSystem

    CombatSystem().on_tick(state, TickReport(2, ""))
    assert state.nations["vosk"].military_morale == pytest.approx(before - 4)


# --- 5. save / load --------------------------------------------------------------------------------------


@pytest.mark.live
def test_save_and_load_resume_the_exact_campaign(tmp_path):
    state = new_game(seed=21)
    engine = build_default_engine(state)
    issue_move_order(state, "kes_1_dd", (121, 14))
    wrecked = sabotage(state, state.unit("vosk_west_hq"), (175, 27), 1.0)  # Karzan's railway yards
    assert wrecked
    for _ in range(10):
        settle_dilemma(state)
        engine.advance()
    jam(state, state.unit("kes_1_inf").location, 3, 2)
    state.forced_card = "worker_strike"
    path = save_game(state, tmp_path / "war.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["turn"] == state.clock.turn

    loaded = load_game(path)
    assert snapshot(loaded)["state"] == snapshot(state)["state"]
    assert loaded.player is loaded.nations["kestria"]
    assert loaded.map_damage and all(loaded.world_map.transport_at(*c) == "destroyed_rail"  # map damage survives
                                     for c in wrecked if c in loaded.map_damage)
    assert is_dark(loaded, loaded.unit("kes_1_inf"))
    # Both campaigns continue identically: the same dice, the same war.
    engine2 = build_default_engine(loaded)
    for _ in range(4):
        for s in (state, loaded):
            settle_dilemma(s)
        engine.advance()
        engine2.advance()
    assert snapshot(loaded)["state"] == snapshot(state)["state"]


def test_bad_saves_are_refused(tmp_path):
    with pytest.raises(SaveError):
        load_game(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": 99, "state": {}}), encoding="utf-8")
    with pytest.raises(SaveError):
        load_game(bad)


# --- UI ------------------------------------------------------------------------------------------------------


def test_save_hotkey_load_flag_relieve_key_and_lost_contacts(tmp_path):
    from textual.widgets import DataTable, OptionList

    from src.ui.app import CommandTerminalApp
    from src.ui.widgets.map_canvas import MapCanvas

    path = tmp_path / "savegame.json"

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=5, save_path=str(path))
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            game = app.game
            await pilot.press("n")
            await pilot.pause()
            # Relieve K-01's commander from the Military screen.
            await pilot.press("3")
            await pilot.pause()
            table = app.screen.query_one("#mil-formations", DataTable)
            table.focus()
            keys = [r.key.value for r in table.ordered_rows]
            table.move_cursor(row=keys.index("kes_1_inf"))
            morale = game.player.military_morale
            await pilot.press("f")
            await pilot.pause()
            assert game.unit("kes_1_inf").commander != "Maj. Gen. Oskar Hale"
            assert game.player.military_morale < morale
            # Jam K-01: it leaves the Order of Battle and turns into [?] on the map.
            jam(game, game.unit("kes_1_inf").location, 2, 2)
            app.state_changed()
            await pilot.press("4")
            await pilot.pause()
            orbat = app.screen.query_one("#orbat-list", OptionList)
            assert "kes_1_inf" not in {o.id for o in orbat.options}
            canvas = app.screen.query_one(MapCanvas)
            canvas.jump_to(121, 20)
            await pilot.pause()
            assert "CONTACT LOST" in str(app.screen.query_one("#intel-readout").render())
            # CTRL+S saves.
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert path.exists()
            return game.clock.turn, game.unit("kes_1_inf").commander

    turn, commander = asyncio.run(run())

    async def resume():
        app = CommandTerminalApp(skip_boot=True, load=str(path))
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            assert app.game.clock.turn == turn
            assert app.game.unit("kes_1_inf").commander == commander

    asyncio.run(resume())


def test_victory_modal():
    from src.ui.app import CommandTerminalApp
    from src.ui.screens.game_over import GameOverScreen

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=6)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            vosk = app.game.nations["vosk"]
            vosk.treasury, vosk.military_morale = -10_000_000, 0  # beyond what a week of taxes can mend
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, GameOverScreen) and app.screen.victory
            assert "VICTORY: ENEMY CAPITULATION" in str(app.screen.query_one("#gameover-title").render())

    asyncio.run(run())


def test_patched_cost_tables_match_a_full_rebuild(quiet):
    from src.engine.engineering import invalidate_terrain_caches
    from src.engine.logistics_engine import compute_network, entry_costs
    from src.engine.movement import move_costs

    state, _ = quiet
    compute_network(state, "kestria")  # warm every cache
    move_costs(state, "land")
    sabotage(state, state.unit("kes_frontier_hq"), (110, 36), 1.0)
    patched = (dict(move_costs(state, "land")), dict(entry_costs(state, "kestria")),
               compute_network(state, "kestria")["primary"])
    invalidate_terrain_caches(state)  # throw everything away and rebuild
    rebuilt = (move_costs(state, "land"), entry_costs(state, "kestria"), compute_network(state, "kestria")["primary"])
    assert patched == rebuilt
