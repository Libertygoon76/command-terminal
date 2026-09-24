"""Phase 5: the Military Industrial Complex, the resupply pipeline, and tactical combat."""

import asyncio
import random

import pytest

from src.engine.combat_engine import (
    CombatSystem,
    StanceError,
    _choose_ai_stance,
    defense,
    firepower,
    set_stance,
)
from src.engine.data_loader import new_game
from src.engine.economy_engine import compute_ledger
from src.engine.event_manager import respond
from src.engine.logistics_engine import establishment, fill_ratio, update_supply
from src.engine.movement import OrderError, _engage, issue_move_order
from src.engine.production import ProductionError, ai_rebalance, assign_factories, forecast, run_production
from src.engine.systems import TickReport
from src.engine.tick_engine import build_default_engine
from src.models import ASSAULT, DEFEND, ENGAGED, ROUTING, WITHDRAW


@pytest.fixture
def quiet():
    state = new_game(seed=21)
    state.ai_states.clear()
    return state, build_default_engine(state)


def engage(state, a_id, b_id):
    a, b = state.unit(a_id), state.unit(b_id)
    _engage(state, a, b, [])
    return a, b


# --- production -----------------------------------------------------------------------


def test_assign_factories_rules(quiet):
    state, _ = quiet
    nation = state.player
    free = nation.free_factories
    assert free > 0
    assert assign_factories(state, nation.id, "howitzer_152", 1) == 1
    assert nation.free_factories == free - 1
    assert nation.line_efficiency["howitzer_152"] == pytest.approx(0.5)  # a new line starts slow
    with pytest.raises(ProductionError):
        assign_factories(state, nation.id, "mbt_medium", 1)  # needs Medium Tank Chassis research
    with pytest.raises(ProductionError):
        assign_factories(state, nation.id, "rifle_762", nation.free_factories + 1)
    assign_factories(state, nation.id, "howitzer_152", -1)
    assert "howitzer_152" not in nation.production and "howitzer_152" not in nation.line_efficiency


def test_factories_deposit_output_and_consume_resources(quiet):
    state, _ = quiet
    nation = state.player
    before_stock = nation.national_stockpile.get("ammo_762", 0)
    expected = int(forecast(state, nation, "ammo_762"))
    munitions = nation.stockpiles["munitions"] + nation.resource_output["munitions"]
    report = run_production(state, nation)
    assert report["produced"]["ammo_762"] == expected
    assert nation.national_stockpile["ammo_762"] == before_stock + expected
    assert nation.stockpiles["munitions"] < munitions


def test_raw_material_shortage_limits_output(quiet):
    state, _ = quiet
    nation = state.player
    nation.stockpiles["steel"] = 0
    nation.resource_output["steel"] = 0
    report = run_production(state, nation)
    assert report["produced"]["rifle_762"] == 0
    assert "steel" in report["shortages"]["rifle_762"]


def test_line_efficiency_grows_and_factories_cost_upkeep(quiet):
    state, _ = quiet
    nation = state.player
    base_expenses = compute_ledger(state).total_expenses
    assign_factories(state, nation.id, "howitzer_152", 2)
    assert compute_ledger(state).total_expenses == base_expenses + 2 * 1100
    run_production(state, nation)
    assert nation.line_efficiency["howitzer_152"] == pytest.approx(0.6)


def test_ai_rebalances_within_its_factories():
    state = new_game(seed=4)
    vosk = state.nations["vosk"]
    for unit in vosk.units:
        unit.equipment_inventory["shell_152_he"] = 0
    ai_rebalance(state, vosk)
    assert vosk.assigned_factories <= vosk.military_factories
    assert vosk.production.get("shell_152_he", 0) > 0


# --- resupply pipeline ------------------------------------------------------------------


def test_supplied_units_draw_from_national_stockpile(quiet):
    state, _ = quiet
    unit = state.unit("kes_2_inf")
    unit.equipment_inventory["ammo_762"] = 0
    stock = state.player.national_stockpile["ammo_762"]
    update_supply(state)
    got = unit.equipment_inventory["ammo_762"]
    assert got > 0
    assert state.player.national_stockpile["ammo_762"] <= stock - got + 0  # fuel etc. may also move
    assert got <= establishment(state, unit)["ammo_762"]


def test_empty_depot_delivers_nothing(quiet):
    state, _ = quiet
    unit = state.unit("kes_2_inf")
    unit.equipment_inventory["ammo_762"] = 0
    state.player.national_stockpile["ammo_762"] = 0
    update_supply(state)
    assert unit.equipment_inventory["ammo_762"] == 0


def test_cut_off_units_get_no_equipment(quiet):
    state, _ = quiet
    state.config["logistics"]["zoc_radius"] = 3
    unit = state.unit("kes_7_arm")
    state.unit("vosk_4_rifle").location = (62, 21)
    unit.equipment_inventory["shell_76"] = 0
    update_supply(state)
    assert unit.supply_state == "isolated" and unit.equipment_inventory["shell_76"] == 0


def test_replacements_come_from_the_manpower_pool(quiet):
    state, _ = quiet
    before = {u.id: u.strength for u in state.player.units}
    pool = state.player.manpower
    update_supply(state)
    assert state.unit("kes_1_inf").strength > before["kes_1_inf"]  # 9,400 of 10,000: topped up
    gained = sum(u.strength - before[u.id] for u in state.player.units)
    assert state.player.manpower == pool - gained


# --- combat -----------------------------------------------------------------------------


def test_combat_drains_ammunition_and_kills(quiet):
    state, _ = quiet
    state.unit("kes_1_inf").location = (76, 7)
    ours, theirs = engage(state, "kes_1_inf", "vosk_4_rifle")
    ammo = (ours.equipment_inventory["ammo_762"], ours.equipment_inventory["shell_152_he"])
    enemy_ammo = theirs.equipment_inventory["ammo_762"]
    men = (ours.strength, theirs.strength)
    rifles = ours.equipment_inventory["rifle_762"]
    report = TickReport(2, "")
    CombatSystem().on_tick(state, report)
    assert ours.equipment_inventory["ammo_762"] < ammo[0]
    assert ours.equipment_inventory["shell_152_he"] < ammo[1]
    assert theirs.equipment_inventory["ammo_762"] < enemy_ammo
    assert ours.strength < men[0] and theirs.strength < men[1]
    assert ours.equipment_inventory["rifle_762"] < rifles  # weapons die with their crews
    battle = next(iter(state.battles.values()))
    assert battle.ammo_expended["kestria"]["ammo_762"] > 0 and battle.casualties["vosk"] > 0
    assert any(m.template_id == "sitrep" for m in report.new_messages)


def test_empty_armor_cannot_fight(quiet):
    state, _ = quiet
    tank = state.unit("kes_7_arm")
    full = firepower(state, tank, consume=False)
    tank.equipment_inventory["shell_76"] = 0
    tank.equipment_inventory["fuel_drums"] = 0
    tank.equipment_inventory["ammo_762"] = 0
    dry = firepower(state, tank, consume=False)
    assert full.hard > 100 and dry.hard == 0
    assert (dry.soft + dry.hard) < 0.02 * (full.soft + full.hard) and dry.starved


def test_no_fuel_grounds_tanks_even_with_shells(quiet):
    state, _ = quiet
    tank = state.unit("kes_7_arm")
    tank.equipment_inventory["fuel_drums"] = 0
    assert firepower(state, tank, consume=False).hard == 0


def test_terrain_trenches_and_stance_raise_defense(quiet):
    state, _ = quiet
    unit = state.unit("kes_2_inf")  # (69, 21): plains, next to the trench line at x=71? no: 2 columns away
    unit.stance = DEFEND
    unit.location = (70, 21)  # beside the Kestrian trench line
    entrenched = defense(state, unit)
    unit.location = (40, 26)  # open river valley, no trench
    open_ground = defense(state, unit)
    unit.location = (60, 10)  # Stonereach mountains
    mountain = defense(state, unit)
    assert entrenched > open_ground and mountain > open_ground
    unit.location = (40, 26)
    unit.stance = ASSAULT
    assert defense(state, unit) < open_ground


def test_assault_hits_harder_but_burns_more_ammo(quiet):
    state, _ = quiet
    a, b = state.unit("kes_1_inf"), state.unit("kes_3_inf")
    b.equipment_inventory = dict(a.equipment_inventory)
    a.stance, b.stance = ASSAULT, DEFEND
    fa, fb = firepower(state, a), firepower(state, b)
    assert fa.expended["ammo_762"] > fb.expended["ammo_762"]


def test_rout_retreats_and_victor_takes_the_cell(quiet):
    state, _ = quiet
    state.unit("kes_1_inf").location = (76, 7)
    ours, theirs = engage(state, "kes_1_inf", "vosk_4_rifle")
    theirs.morale = 15.5  # one more week of fire will break them
    origin = theirs.location
    report = TickReport(2, "")
    CombatSystem().on_tick(state, report)
    assert theirs.status == ROUTING and theirs.routing_weeks > 0
    assert theirs.location != origin and ours.location == origin
    assert not theirs.engaged_with and not ours.engaged
    battle = next(iter(state.battles.values()))
    assert not battle.active and battle.victor == "kestria"
    aar = [m for m in report.new_messages if m.template_id == "after_action_report"]
    assert aar and "VICTORY" in aar[0].subject and "CASUALTIES" in aar[0].body
    with pytest.raises(OrderError):
        issue_move_order(state, theirs.id, (90, 7), nation_id="vosk")  # routing units ignore orders


def test_destroyed_units_leave_the_order_of_battle(quiet):
    state, _ = quiet
    state.unit("kes_1_inf").location = (76, 7)
    ours, theirs = engage(state, "kes_1_inf", "vosk_4_rifle")
    theirs.strength = 3
    CombatSystem().on_tick(state, TickReport(2, ""))
    assert state.unit("vosk_4_rifle") is None
    battle = next(iter(state.battles.values()))
    assert "vosk_4_rifle" in battle.destroyed and battle.victor == "kestria"


def test_withdraw_breaks_contact_and_ends_battle(quiet):
    state, engine = quiet
    state.unit("kes_1_inf").location = (76, 7)
    ours, theirs = engage(state, "kes_1_inf", "vosk_4_rifle")
    set_stance(state, ours.id, WITHDRAW)
    for _ in range(4):
        engine.advance()
        if not ours.engaged:
            break
    assert not ours.engaged and ours.status != ROUTING
    battle = next(iter(state.battles.values()))
    assert not battle.active and battle.victor == "vosk"  # they hold the field


def test_multi_week_battle_sends_weekly_sitreps(quiet):
    state, engine = quiet
    state.unit("kes_1_inf").location = (76, 7)
    engage(state, "kes_1_inf", "vosk_4_rifle")
    for _ in range(3):
        engine.advance()
    sitreps = [m for m in state.inbox.messages if m.template_id == "sitrep"]
    assert len(sitreps) >= 2
    assert "Enemy casualties" in sitreps[0].body and "(est.)" in sitreps[0].body


def test_stance_rules():
    state = new_game(seed=2)
    with pytest.raises(StanceError):
        set_stance(state, "vosk_4_rifle", ASSAULT)  # not ours
    unit = state.unit("kes_1_inf")
    set_stance(state, unit.id, ASSAULT)
    assert unit.stance == ASSAULT
    assert _choose_ai_stance(state, unit, 30000, 10000) == ASSAULT
    assert _choose_ai_stance(state, unit, 5000, 10000) == WITHDRAW
    assert _choose_ai_stance(state, unit, 10000, 10000) == DEFEND


@pytest.mark.parametrize("seed", range(8))
def test_fuzzed_war_keeps_invariants(seed):
    state = new_game(seed=seed)
    engine = build_default_engine(state)
    rng = random.Random(seed)
    for _ in range(30):
        for email in state.inbox.awaiting_response():
            if rng.random() < 0.5:
                respond(state, email.id, rng.choice(email.options).id)
        for unit in state.player.units:
            if unit.routing:
                continue
            if rng.random() < 0.3:
                try:
                    issue_move_order(state, unit.id, (rng.randint(60, 90), rng.randint(5, 30)))
                except OrderError:
                    pass
            if rng.random() < 0.2:
                set_stance(state, unit.id, rng.choice([DEFEND, ASSAULT, WITHDRAW]))
        engine.advance()
        if state.game_over:
            break
        ids = {u.id for u in state.all_units()}
        for nation in state.nations.values():
            assert all(v >= 0 for v in nation.national_stockpile.values())
            assert all(v >= 0 for v in nation.stockpiles.values())
            assert nation.assigned_factories <= nation.military_factories
        for unit in state.all_units():
            assert unit.strength > 0
            assert all(v >= 0 for v in unit.equipment_inventory.values())
            assert set(unit.engaged_with) <= ids
            if unit.routing:
                assert not unit.engaged_with
            assert (unit.status == ENGAGED) == bool(unit.engaged_with)
        for pair in state.engagements:
            assert pair <= ids


# --- UI ---------------------------------------------------------------------------------


def test_economy_screen_assigns_factories_reactively():
    from textual.widgets import DataTable

    from src.ui.app import CommandTerminalApp

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=5)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.pause()
            table = app.screen.query_one("#econ-production", DataTable)
            assert table.has_focus
            keys = [row.key.value for row in table.ordered_rows]
            table.move_cursor(row=keys.index("howitzer_152"))
            await pilot.press("plus")
            await pilot.press("plus")
            await pilot.pause()
            assert app.game.player.production.get("howitzer_152") == 2
            assert str(table.get_row("howitzer_152")[2]) == "2"  # table redrawn, cursor kept
            await pilot.press("minus")
            await pilot.pause()
            assert app.game.player.production.get("howitzer_152") == 1
            table.move_cursor(row=keys.index("mbt_medium"))
            await pilot.press("plus")  # locked: refused, nothing changes
            await pilot.pause()
            assert "mbt_medium" not in app.game.player.production
            table.move_cursor(row=keys.index("howitzer_152"))
            await pilot.press("0")
            await pilot.pause()
            assert "howitzer_152" not in app.game.player.production

    asyncio.run(run())


def test_war_room_stance_key_cycles():
    from src.ui.app import CommandTerminalApp
    from src.ui.widgets.map_canvas import MapCanvas

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=5)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            await pilot.press("4")
            await pilot.pause()
            canvas = app.screen.query_one(MapCanvas)
            canvas.jump_to(69, 8)
            await pilot.pause()
            unit = app.game.unit("kes_1_inf")
            assert unit.stance == DEFEND
            await pilot.press("t")
            await pilot.pause()
            assert unit.stance == ASSAULT
            await pilot.press("t")
            await pilot.pause()
            assert unit.stance == WITHDRAW

    asyncio.run(run())
