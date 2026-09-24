"""Phase 6: taxation & bankruptcy, recruitment & training, research & development, the purge lock."""

import asyncio

import pytest

from src.engine.combat_engine import defense
from src.engine.data_loader import new_game
from src.engine.economy_engine import EconomyError, compute_ledger, run_economy, set_tax_policy, shift_tax_policy
from src.engine.logistics_engine import establishment, fill_ratio
from src.engine.map_overlay import build_markers
from src.engine.production import ProductionError, assign_factories, forecast
from src.engine.recruitment import RecruitmentError, cancel_training, muster_point, raise_formation
from src.engine.research import ResearchError, ResearchSystem, start_research, status, weeks_remaining
from src.engine.systems import TickReport
from src.engine.tick_engine import build_default_engine


@pytest.fixture
def quiet():
    state = new_game(seed=31)
    state.ai_states.clear()
    return state, build_default_engine(state)


# --- economy & taxation ---------------------------------------------------------------


def test_trade_income_closes_the_deficit(quiet):
    state, _ = quiet
    ledger = compute_ledger(state)
    assert "Trade & industry" in ledger.income
    assert ledger.net > 0  # the default war economy is no longer bleeding at Normal taxes


def test_tax_policy_trades_income_for_morale(quiet):
    state, _ = quiet
    nation = state.player
    normal = compute_ledger(state).total_income
    set_tax_policy(state, nation.id, "oppressive")
    assert nation.tax_rate == pytest.approx(0.36)
    assert compute_ledger(state).total_income > normal
    morale = nation.morale
    run_economy(state, nation)
    assert nation.morale == pytest.approx(morale - 2.2)
    set_tax_policy(state, nation.id, "low")
    morale = nation.morale
    run_economy(state, nation)
    assert nation.morale == pytest.approx(morale + 0.6)
    with pytest.raises(EconomyError):
        shift_tax_policy(state, nation.id, -1)  # already lowest


def test_bankruptcy_drains_both_morales(quiet):
    state, engine = quiet
    nation = state.player
    nation.treasury = -500_000
    civil, military = nation.morale, nation.military_morale
    run_economy(state, nation)
    assert nation.bankrupt
    assert nation.morale == pytest.approx(civil - 4)
    assert nation.military_morale == pytest.approx(military - 6)
    nation.treasury = 0
    assert nation.bankrupt  # exactly zero counts


# --- recruitment ----------------------------------------------------------------------


def test_raise_new_formation_spawns_at_capital_underequipped(quiet):
    state, engine = quiet
    nation = state.player
    treasury, pool = nation.treasury, nation.manpower
    order = raise_formation(state, nation.id, "infantry_division")
    assert nation.treasury == treasury - 60000 and nation.manpower == pool - 10000
    assert order.name == "4th Kestrian Infantry Division" and order.designation == "K-09"
    for _ in range(order.weeks_total - 1):
        engine.advance()
        assert state.unit(order.id) is None
    engine.advance()
    unit = state.unit(order.id)
    assert unit is not None and unit.location == muster_point(state, nation) == (45, 30)
    assert unit.strength == 10000 and 60 <= unit.morale <= 62  # green troops (+2 rest in the rear)
    assert fill_ratio(state, unit, ("small_arms", "artillery")) < 0.6  # weapons arrive slowly: not combat-ready
    assert any(m.template_id == "formation_ready" for m in state.inbox.messages)
    assert any(unit in m.units for m in build_markers(state))  # it is on the map
    ammo = unit.equipment_inventory.get("ammo_762", 0)
    engine.advance()
    assert unit.equipment_inventory["ammo_762"] > ammo  # logistics fills it from the stockpile


def test_recruitment_limits_and_cancel(quiet):
    state, _ = quiet
    nation = state.player
    nation.treasury = 1000
    with pytest.raises(RecruitmentError):
        raise_formation(state, nation.id, "infantry_division")
    nation.treasury = 10_000_000
    nation.manpower = 500
    with pytest.raises(RecruitmentError):
        raise_formation(state, nation.id, "infantry_division")
    nation.manpower = 50_000
    order = raise_formation(state, nation.id, "armored_brigade")
    treasury = nation.treasury
    cancel_training(state, order.id)
    assert nation.manpower == 50_000 and nation.treasury == treasury + order.cost // 2
    assert not state.training


def test_names_and_designations_stay_unique(quiet):
    state, _ = quiet
    state.player.treasury = 10_000_000
    a = raise_formation(state, "kestria", "infantry_division")
    b = raise_formation(state, "kestria", "infantry_division")
    assert a.name != b.name and a.designation != b.designation


def test_ai_recruits_after_losses():
    state = new_game(seed=8)
    engine = build_default_engine(state)
    vosk = state.nations["vosk"]
    for unit in vosk.units[:4]:
        unit.strength = 1000
    engine.advance()
    assert any(o.nation_id == "vosk" for o in state.training)


# --- research -------------------------------------------------------------------------


def test_research_completes_unlocks_production_and_upgrades_kit(quiet):
    state, engine = quiet
    nation = state.player
    with pytest.raises(ProductionError):
        assign_factories(state, nation.id, "kevlar_vest", 1)
    start_research(state, nation.id, "kevlar_armor")
    k01 = state.unit("kes_1_inf")
    assert "kevlar_vest" not in establishment(state, k01)
    weeks = int(weeks_remaining(state, nation, "kevlar_armor"))
    report = TickReport(2, "")
    for _ in range(weeks):
        ResearchSystem().on_tick(state, report)
    assert status(state, nation, "kevlar_armor") == "KNOWN" and nation.research_project is None
    assert any(m.subject == "R&D BREAKTHROUGH: Aramid-Fibre Body Armor" for m in report.new_messages)
    assert assign_factories(state, nation.id, "kevlar_vest", 1) == 1  # the line is now open
    assert establishment(state, k01)["kevlar_vest"] > 0  # every man should now carry a vest


def test_research_costs_money_and_needs_prerequisites(quiet):
    state, _ = quiet
    nation = state.player
    with pytest.raises(ResearchError):
        start_research(state, nation.id, "apfsds_penetrator")  # needs Medium Tank Chassis
    before = compute_ledger(state).total_expenses
    start_research(state, nation.id, "medium_tank_chassis")
    assert compute_ledger(state).total_expenses == before + 12000


def test_progress_is_kept_when_switching_and_halts_when_bankrupt(quiet):
    state, _ = quiet
    nation = state.player
    system = ResearchSystem()
    start_research(state, nation.id, "image_intensifier")
    for _ in range(3):
        system.on_tick(state, TickReport(2, ""))
    start_research(state, nation.id, "kevlar_armor")
    start_research(state, nation.id, "image_intensifier")
    assert nation.research_progress["image_intensifier"] == 3
    nation.treasury = 0
    system.on_tick(state, TickReport(2, ""))
    assert nation.research_progress["image_intensifier"] == 3  # labs unpaid


def test_tech_effects_apply(quiet):
    state, _ = quiet
    nation = state.player
    base = forecast(state, nation, "ammo_762")
    unit = state.unit("kes_2_inf")
    unit.stance = "defend"
    base_def = defense(state, unit)
    for tech in ("assembly_line_tooling", "defense_in_depth_doctrine"):
        start_research(state, nation.id, tech)
        for _ in range(int(weeks_remaining(state, nation, tech))):
            ResearchSystem().on_tick(state, TickReport(2, ""))
    assert forecast(state, nation, "ammo_762") == pytest.approx(base * 1.1)
    assert defense(state, unit) == pytest.approx(base_def * 1.1)


def test_ai_researches_on_its_own():
    state = new_game(seed=9)
    engine = build_default_engine(state)
    engine.advance()
    assert state.nations["vosk"].research_project is not None


# --- UI -------------------------------------------------------------------------------


def _app():
    from src.ui.app import CommandTerminalApp

    return CommandTerminalApp(skip_boot=True, seed=12)


def test_economy_tax_keys():
    async def run():
        app = _app()
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            await pilot.press("2")
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert app.game.player.tax_policy == "high"
            await pilot.press("left_square_bracket", "left_square_bracket")
            await pilot.pause()
            assert app.game.player.tax_policy == "low" and app.game.player.tax_rate == pytest.approx(0.12)

    asyncio.run(run())


def test_military_screen_raises_and_cancels():
    from textual.widgets import DataTable

    async def run():
        app = _app()
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            await pilot.press("3")
            await pilot.pause()
            recruit = app.screen.query_one("#mil-recruit", DataTable)
            assert recruit.has_focus
            keys = [r.key.value for r in recruit.ordered_rows]
            recruit.move_cursor(row=keys.index("infantry_division"))
            await pilot.press("r")
            await pilot.pause()
            assert len(app.game.training) == 1
            queue = app.screen.query_one("#mil-training", DataTable)
            assert queue.row_count == 1
            queue.focus()
            await pilot.press("x")
            await pilot.pause()
            assert not app.game.training and queue.row_count == 0

    asyncio.run(run())


def test_research_screen_starts_project():
    from textual.widgets import DataTable, Static

    async def run():
        app = _app()
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            await pilot.press("5")
            await pilot.pause()
            table = app.screen.query_one("#rnd-table", DataTable)
            assert table.has_focus
            keys = [r.key.value for r in table.ordered_rows]
            table.move_cursor(row=keys.index("kevlar_armor"))
            await pilot.press("enter")
            await pilot.pause()
            assert app.game.player.research_project == "kevlar_armor"
            assert "ARAMID" in str(app.screen.query_one("#sys-info", Static).render())
            await pilot.press("x")
            await pilot.pause()
            assert app.game.player.research_project is None

    asyncio.run(run())
