"""Phase 8 (revised brief): the home front — epidemics, natural disasters, relief duty, emergencies —
and their survival through save / load."""

import asyncio

import pytest

from src.engine import crisis_engine
from src.engine.combat_engine import StanceError, set_stance
from src.engine.data_loader import new_game
from src.engine.dilemmas import card, resolve
from src.engine.engineering import EngineeringSystem, damaged_cells
from src.engine.movement import OrderError, issue_move_order
from src.engine.production import forecast
from src.engine.research import ResearchSystem, start_research, weeks_remaining
from src.engine.savegame import load_game, save_game, snapshot
from src.engine.systems import TickReport
from src.engine.tick_engine import DilemmaPendingError, build_default_engine
from src.models import ASSAULT
from tests.conftest import settle_dilemma


@pytest.fixture
def quiet():
    state = new_game(seed=91)
    state.ai_states.clear()
    state.catalog["crises"]["outbreak_chance"] = 0.0
    state.catalog["crises"]["disaster_chance"] = 0.0
    return state, build_default_engine(state)


def contagious(state, disease="cholera"):
    state.catalog["crises"]["diseases"][disease]["spread_chance"] = 1.0
    state.catalog["crises"]["diseases"][disease]["burnout_chance"] = 0.0


# --- epidemics -------------------------------------------------------------------------------------------


def test_outbreak_raises_an_emergency_and_takes_its_toll(quiet):
    state, engine = quiet
    output = forecast(state, state.player, "ammo_762")
    outbreak = crisis_engine.start_outbreak(state, "cholera", "city:Aldmark")
    entry = card(state, state.pending_dilemma)
    assert entry["emergency"] and "CHOLERA OUTBREAK" in entry["title"]
    assert [c["id"] for c in entry["choices"]] == ["quarantine", "cordon", "ignore"]
    with pytest.raises(DilemmaPendingError):
        engine.advance()  # the emergency pauses the game
    resolve(state, "ignore")
    assert forecast(state, state.player, "ammo_762") < output  # the capital's workers are sick
    morale = state.player.morale
    crisis_engine.run_epidemics(state, TickReport(2, ""))
    assert state.player.morale <= morale - 2.0
    assert crisis_engine.outbreak_sites(state, outbreak)


def test_infected_formations_die_in_their_beds(quiet):
    state, _ = quiet
    k01 = state.unit("kes_1_inf")
    crisis_engine.start_outbreak(state, "trench_typhus", f"unit:{k01.id}")
    resolve(state, "ignore")
    men = k01.strength
    crisis_engine.run_epidemics(state, TickReport(2, ""))
    assert k01.strength <= men * 0.975  # 3% a week, no battle required


def test_unchecked_disease_spreads_until_quarantine_cures_it(quiet):
    state, _ = quiet
    contagious(state)
    outbreak = crisis_engine.start_outbreak(state, "cholera", "city:Aldmark")
    resolve(state, "ignore")
    for week in range(3):
        state.clock.advance()
        crisis_engine.run_epidemics(state, TickReport(state.clock.turn, ""))
    assert len(crisis_engine.outbreak_sites(state, outbreak)) >= 3
    assert state.pending_dilemma and "SPREADING" in card(state, state.pending_dilemma)["title"]  # it comes back
    treasury = state.player.treasury
    cost = crisis_engine.quarantine_cost(state, outbreak)
    resolve(state, "quarantine")
    assert state.player.treasury == treasury - cost
    sites = len(crisis_engine.outbreak_sites(state, outbreak))
    crisis_engine.run_epidemics(state, TickReport(6, ""))
    assert len(crisis_engine.outbreak_sites(state, outbreak)) == sites  # contained: nothing new spreads
    crisis_engine.run_epidemics(state, TickReport(7, ""))
    assert not crisis_engine.outbreak_sites(state, outbreak)  # cleared in two weeks, nothing spread


def test_cordon_stops_the_spread_but_cures_nobody(quiet):
    state, _ = quiet
    contagious(state)
    outbreak = crisis_engine.start_outbreak(state, "cholera", "city:Aldmark")
    resolve(state, "cordon")
    for week in range(4):
        crisis_engine.run_epidemics(state, TickReport(2 + week, ""))
    assert crisis_engine.outbreak_sites(state, outbreak) == ["city:Aldmark"]


def test_field_medicine_clears_every_infection(quiet):
    state, _ = quiet
    crisis_engine.start_outbreak(state, "trench_typhus", "unit:kes_2_inf")
    resolve(state, "ignore")
    start_research(state, state.player.id, "field_medicine")
    for _ in range(int(weeks_remaining(state, state.player, "field_medicine"))):
        ResearchSystem().on_tick(state, TickReport(2, ""))
    for week in range(2):
        crisis_engine.run_epidemics(state, TickReport(3 + week, ""))
    assert not state.infections


# --- natural disasters ------------------------------------------------------------------------------------


def test_disaster_wrecks_infrastructure_and_offers_three_hard_choices(quiet):
    state, _ = quiet
    before = len(state.map_damage)
    card_id = crisis_engine.start_disaster(state, "severe_flooding", "greywater")
    assert card_id and state.pending_dilemma == card_id
    assert len(state.map_damage) > before  # rails became [x]
    entry = card(state, card_id)
    assert entry["emergency"] and [c["id"] for c in entry["choices"]] == ["relief", "military", "ignore"]
    treasury = state.player.treasury
    resolve(state, "relief")
    assert state.player.treasury == treasury - 50000


def test_deploying_the_military_takes_a_formation_out_of_the_war(quiet):
    state, engine = quiet
    card_id = crisis_engine.start_disaster(state, "earthquake", "stonereach")
    choice = next(c for c in card(state, card_id)["choices"] if c["id"] == "military")
    unit = state.unit(choice["effects"]["relief_unit"]["unit"])
    morale = state.player.morale
    resolve(state, "military")
    assert unit.relief_weeks == 3 and state.player.morale == pytest.approx(morale + 4)
    with pytest.raises(OrderError):
        issue_move_order(state, unit.id, (100, 40))
    with pytest.raises(StanceError):
        set_stance(state, unit.id, ASSAULT)
    for _ in range(3):
        engine.advance()
    assert unit.relief_weeks == 0
    issue_move_order(state, unit.id, (100, 40))  # back on duty


def test_ignoring_a_disaster_costs_massive_morale(quiet):
    state, _ = quiet
    crisis_engine.start_disaster(state, "severe_flooding", "westmarch")
    morale = state.player.morale
    resolve(state, "ignore")
    assert state.player.morale == pytest.approx(morale - 8)


def test_two_emergencies_in_one_week_queue_up(quiet):
    state, _ = quiet
    crisis_engine.start_outbreak(state, "cholera", "city:Harrow")
    crisis_engine.start_disaster(state, "severe_flooding", "greywater")
    first = state.pending_dilemma
    assert state.dilemma_queue
    resolve(state, "ignore")
    assert state.pending_dilemma and state.pending_dilemma != first
    resolve(state, "relief")
    assert state.pending_dilemma is None


def test_engineers_restore_a_flooded_line(quiet):
    state, _ = quiet
    rubble = set(damaged_cells(state))
    crisis_engine.start_disaster(state, "severe_flooding", "greywater")
    resolve(state, "relief")
    wrecked = set(damaged_cells(state)) - rubble
    assert wrecked
    eng = state.unit("kes_1_eng")
    from src.engine.movement import scaled_distance

    eng.location = min(wrecked, key=lambda c: (max(scaled_distance(state, c, w) for w in wrecked), c))
    eng.supply_state = "supplied"
    for _ in range(4):  # 3 sections a week: a disaster takes the engineers 2-3 weeks
        EngineeringSystem().on_tick(state, TickReport(2, ""))
    assert not (set(damaged_cells(state)) - rubble)


# --- save / load ----------------------------------------------------------------------------------------------


@pytest.mark.live
def test_crises_survive_save_and_load(tmp_path):
    state = new_game(seed=33)
    engine = build_default_engine(state)
    crisis_engine.start_outbreak(state, "trench_typhus", "unit:kes_1_inf")
    resolve(state, "ignore")
    answered = crisis_engine.start_disaster(state, "earthquake", "stonereach")
    resolve(state, "military")
    crisis_engine.start_disaster(state, "severe_flooding", "greywater")  # left on screen, unanswered
    path = save_game(state, tmp_path / "crisis.json")
    loaded = load_game(path)
    assert snapshot(loaded)["state"] == snapshot(state)["state"]
    assert loaded.infections == state.infections and loaded.pending_dilemma == state.pending_dilemma
    assert card(loaded, loaded.pending_dilemma)["emergency"]
    assert any(u.relief_weeks for u in loaded.player.units)
    assert answered not in loaded.crisis_cards
    engine2 = build_default_engine(loaded)
    for _ in range(5):
        settle_dilemma(state)
        settle_dilemma(loaded)
        engine.advance()
        engine2.advance()
    assert snapshot(loaded)["state"] == snapshot(state)["state"]


# --- UI -----------------------------------------------------------------------------------------------------------


@pytest.mark.live
def test_emergency_modal_is_red_and_queued_ones_follow():
    from src.ui.app import CommandTerminalApp
    from src.ui.screens.dilemma import DilemmaScreen

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=9, crisis="outbreak")
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            game = app.game
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, DilemmaScreen) and app.screen.emergency
            assert "CRITICAL EMERGENCY" in str(app.screen.query_one("#dilemma-title").render())
            crisis_engine.start_disaster(game, "severe_flooding", "greywater")  # a second one queues behind
            await pilot.press("1")  # fund quarantine
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, DilemmaScreen)
            assert "FLOODING" in str(app.screen.query_one("#dilemma-title").render())
            await pilot.press("3")  # ignore the flood
            await pilot.pause()
            assert game.pending_dilemma is None and not isinstance(app.screen, DilemmaScreen)
            assert all(inf["cure_in"] is not None for inf in game.infections.values())

    asyncio.run(run())
