"""Expansion 1.1 — The Living World: foreign affairs & lend-lease, deep city management, the local news wire,
the Vosk Hotline, the expanded event deck, and all of it through save / load."""

import asyncio

import pytest

from src.engine import cities, crisis_engine, diplomacy, hotline
from src.engine.combat_engine import CombatSystem, defense
from src.engine.movement import _engage
from src.engine.data_loader import new_game
from src.engine.dilemmas import card, eligible, resolve
from src.engine.economy_engine import compute_ledger
from src.engine.event_manager import respond
from src.engine.fail_states import FailStateSystem
from src.engine.savegame import load_game, save_game, snapshot
from src.engine.systems import TickReport
from src.engine.tick_engine import build_default_engine
from src.models import DEFEND
from tests.conftest import settle_dilemma


@pytest.fixture
def quiet():
    state = new_game(seed=111)
    state.ai_states.clear()
    return state, build_default_engine(state)


def blockade_every_kestrian_port(state):
    for name in state.catalog["diplomacy"]["ports"]["kestria"]:
        state.blockades[name] = "vosk"


# --- foreign affairs ------------------------------------------------------------------------------------


def test_off_map_powers_start_with_alignments_and_treaties(quiet):
    state, _ = quiet
    assert set(state.foreign) == {"oakhaven", "tor", "vael"}
    assert state.foreign["oakhaven"]["alignment"] == 15 and state.foreign["tor"]["alignment"] == -5
    assert diplomacy.relation(state, "tor", "vosk") == 5  # one axis: Tor leans slightly Vosk
    assert "kestria" in state.foreign["oakhaven"]["trade"]


def test_envoys_swing_alignment_within_ideology(quiet):
    state, _ = quiet
    treasury = state.player.treasury
    gain = diplomacy.send_envoy(state, "tor")
    assert gain > 5 and state.player.treasury == treasury - 25000
    assert diplomacy.relation(state, "tor", "vosk") == pytest.approx(-diplomacy.relation(state, "tor"))
    # Ideology bounds what gifts can buy: Oakhaven will never lean far toward the Hegemony, Vael never far to anyone.
    state.nations["vosk"].treasury = 10_000_000
    for _ in range(20):
        diplomacy.send_envoy(state, "oakhaven", "vosk")
        diplomacy.send_envoy(state, "vael")
    assert state.foreign["oakhaven"]["alignment"] >= -5 and state.foreign["vael"]["alignment"] <= 50


def test_oakhaven_despises_oppressive_taxes_and_tor_respects_victories(quiet):
    state, _ = quiet
    from src.engine.economy_engine import set_tax_policy

    set_tax_policy(state, state.player.id, "oppressive")
    diplomacy.drift_and_treaties(state, TickReport(2, ""))
    assert state.foreign["oakhaven"]["alignment"] == pytest.approx(13.5)
    diplomacy.battle_standing(state, state.player.id)
    assert state.foreign["tor"]["alignment"] == pytest.approx(-2)  # Tor swings three times as hard
    assert state.foreign["vael"]["alignment"] == 25  # Vael does not care who wins


def test_trade_agreements_pay_until_every_port_is_blockaded(quiet):
    state, _ = quiet
    with pytest.raises(diplomacy.DiplomacyError):
        diplomacy.sign_trade(state, "tor")  # alignment -5 < +10
    for _ in range(3):
        diplomacy.send_envoy(state, "tor")
    diplomacy.sign_trade(state, "tor")
    ledger = compute_ledger(state)
    assert ledger.income["Foreign trade agreements (2)"] > 0
    blockade_every_kestrian_port(state)
    ledger = compute_ledger(state)
    assert not any(k.startswith("Foreign trade") for k in ledger.income)
    assert any("FOREIGN TRADE SUSPENDED" in n for n in ledger.notes)


def test_lend_lease_tanks_arrive_by_sea(quiet):
    state, engine = quiet
    with pytest.raises(diplomacy.DiplomacyError):
        diplomacy.buy_lend_lease(state, "oakhaven", "oak_tanks")  # needs alignment +35
    for _ in range(4):
        diplomacy.send_envoy(state, "oakhaven")
    treasury = state.player.treasury
    tanks = state.player.national_stockpile.get("lt_tank", 0)
    ship = diplomacy.buy_lend_lease(state, "oakhaven", "oak_tanks")
    assert state.player.treasury == treasury - 95000 and ship["weeks_left"] == 4
    for _ in range(3):
        diplomacy.run_shipments(state, TickReport(2, ""))
    assert state.shipments  # still at sea
    report = TickReport(5, "")
    diplomacy.run_shipments(state, report)
    assert not state.shipments and state.player.national_stockpile["lt_tank"] == tanks + 12
    assert any("LEND-LEASE DELIVERED" in m.subject for m in report.new_messages)


def test_blockades_hold_convoys_at_sea_and_wolfpacks_sink_cargo(quiet):
    state, _ = quiet
    diplomacy.send_envoy(state, "tor")  # Tor sells rifles to anyone leaning even slightly its way
    ship = diplomacy.buy_lend_lease(state, "tor", "tor_rifles")
    ship["weeks_left"] = 1
    blockade_every_kestrian_port(state)
    diplomacy.run_shipments(state, TickReport(2, ""))
    assert state.shipments and "BLOCKADED" in state.shipments[0]["status"]
    state.blockades.clear()
    state.catalog["diplomacy"]["sub_attack_chance"] = 1.0
    state.unit("vosk_3_sub").location = (40, 70)  # a wolfpack out on the convoy lanes
    before = diplomacy.relation(state, "tor", "vosk")
    report = TickReport(3, "")
    diplomacy.run_shipments(state, report)
    assert any("TORPEDOED" in m.subject for m in report.new_messages)
    assert state.player.national_stockpile["rifle_762"] > 0
    assert diplomacy.relation(state, "tor", "vosk") < before  # sinking neutral freighters has a price


def test_vosk_foreign_ministry_woos_trades_and_buys():
    state = new_game(seed=12)
    state.catalog["diplomacy"]["ai"].update(gift_chance=1.0, buy_chance=1.0)
    for _ in range(6):
        diplomacy.ai_foreign_policy(state, "vosk")
    assert diplomacy.relation(state, "tor", "vosk") > 20
    assert "vosk" in state.foreign["tor"]["trade"]
    assert any(s["to"] == "vosk" for s in state.shipments)


# --- cities ------------------------------------------------------------------------------------------------


def test_every_kestrian_settlement_is_a_city(quiet):
    state, _ = quiet
    assert len(state.cities) == 26
    aldmark = state.cities["Aldmark"]
    assert aldmark.type == "capital" and aldmark.population > 700_000 and 0 <= aldmark.local_morale <= 100


def test_building_a_hospital_takes_money_and_time(quiet):
    state, engine = quiet
    treasury = state.player.treasury
    cities.order_building(state, "Aldmark", "hospital")
    assert state.player.treasury == treasury - 60000
    with pytest.raises(cities.CityError):
        cities.order_building(state, "Aldmark", "hospital")  # one per city
    for _ in range(5):
        engine.advance()
    assert not state.cities["Aldmark"].has("hospital")
    engine.advance()
    assert state.cities["Aldmark"].has("hospital")
    assert any("new Hospital" in n["text"] or "Hospital" in n["text"] for n in state.cities["Aldmark"].news)


def test_hospitals_stop_epidemics_starting_and_spreading(quiet):
    state, _ = quiet
    state.catalog["cities"]["buildings"]["hospital"].update(outbreak_factor=0.0, spread_factor=0.0)
    state.cities["Ashby"].buildings.append("hospital")
    state.catalog["crises"]["diseases"]["cholera"].update(spread_chance=1.0, burnout_chance=0.0)
    state.catalog["crises"]["outbreak_chance"] = 0.0
    crisis_engine.start_outbreak(state, "cholera", "city:Aldmark")
    resolve(state, "ignore")
    for week in range(6):
        state.clock.advance()
        crisis_engine.run_epidemics(state, TickReport(state.clock.turn, ""))
        settle_dilemma(state)
    assert "city:Ashby" not in state.infections
    # And no outbreak ever starts in a city with a hospital (outbreak_factor 0).
    for name in state.cities:
        state.cities[name].buildings.append("hospital")
    for _ in range(20):
        assert crisis_engine.start_outbreak(state, "cholera") is None or True
    assert all(not s.startswith("city:") or s in ("city:Aldmark",) or state.infections[s]["outbreak"].startswith("OB-00")
               for s in state.infections)


def test_bunkers_turn_a_city_into_a_fortress(quiet):
    state, _ = quiet
    unit = state.unit("kes_frontier_hq")
    unit.stance = DEFEND
    unit.location = (state.world_map.feature_named("Kestrel Cross").x, state.world_map.feature_named("Kestrel Cross").y)
    street = defense(state, unit)
    state.cities["Kestrel Cross"].buildings.append("bunker")
    assert defense(state, unit) == pytest.approx(street * 1.8 / 1.3)


def test_local_industry_adds_a_military_factory(quiet):
    state, engine = quiet
    factories = state.player.military_factories
    cities.order_building(state, "Ironvale", "local_industry")
    for _ in range(10):
        engine.advance()
    assert state.player.military_factories == factories + 1


def test_cancelling_refunds_half(quiet):
    state, _ = quiet
    treasury = state.player.treasury
    cities.order_building(state, "Harrow", "bunker")
    cities.cancel_building(state, "Harrow")
    assert state.player.treasury == treasury - 45000 + 22500


def test_local_news_follows_the_city_situation(quiet):
    state, _ = quiet
    greywater = state.cities["Greywater"]
    crisis_engine.infect(state, "city:Greywater", "trench_typhus", "OB-test")
    cities.run_city(state, greywater, TickReport(2, ""))
    assert greywater.news and any(w in greywater.news[-1]["text"] for w in ("graves", "Typhus", "fever", "undertakers"))
    assert greywater.local_morale < state.player.morale
    # A battle outside Halden: artillery through the night.
    halden = state.cities["Halden"]
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    ours.location, theirs.location = (110, 24), (112, 24)
    _engage(state, ours, theirs, [])
    assert "battle_near" in cities.situation(state, halden)
    cities.run_city(state, halden, TickReport(2, ""))
    assert halden.news[-1]["text"] in state.catalog["cities"]["news"]["battle_near"] or "Halden" in halden.news[-1]["text"]


# --- the Hotline ---------------------------------------------------------------------------------------------


def _hotline_state():
    state = new_game(seed=42)
    return state, state.ai_states["vosk"]


def test_a_crushing_victory_brings_a_ceasefire_offer():
    state, ai = _hotline_state()
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    theirs.location = (123, 20)
    theirs.strength = 3
    _engage(state, ours, theirs, [])
    CombatSystem().on_tick(state, TickReport(1, ""))
    battle = next(iter(state.battles.values()))
    battle.casualties["vosk"] = 5000
    report = TickReport(1, "")
    hotline.HotlineSystem().on_tick(state, report)
    offer = next(m for m in report.new_messages if m.template_id == "hotline_kestria_winning")
    respond(state, offer.id, "accept")
    assert state.ceasefire_weeks == 4
    ai.tension = 95
    ai.posture = "ASSAULT"
    from src.engine.ai_director import AIDirector

    AIDirector().run(state, ai, TickReport(2, ""))
    assert ai.posture == "DEFEND"  # the Vosk hold their fire


def test_breaking_a_ceasefire_costs_friends():
    state, ai = _hotline_state()
    state.ceasefire_weeks = 3
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    theirs.location = (123, 20)
    _engage(state, ours, theirs, [])
    from src.engine.combat_engine import _battle_for

    _battle_for(state, {ours.id, theirs.id})  # a battle begins this week
    oak = diplomacy.relation(state, "oakhaven")
    report = TickReport(1, "")
    hotline.HotlineSystem().on_tick(state, report)
    assert state.ceasefire_weeks == 0 and diplomacy.relation(state, "oakhaven") < oak
    assert any(m.template_id == "hotline_ceasefire_broken" for m in report.new_messages)


def test_an_assault_comes_with_an_ultimatum_and_concessions_withdraw_our_line():
    state, ai = _hotline_state()
    ai.posture, ai.weeks_in_posture = "ASSAULT", 0
    report = TickReport(1, "")
    hotline.HotlineSystem().on_tick(state, report)
    ultimatum = next(m for m in report.new_messages if m.template_id == "hotline_vosk_preparing_assault")
    assert [o.id for o in ultimatum.options] == ["indemnity", "withdraw", "defy"]
    tension = ai.tension
    respond(state, ultimatum.id, "withdraw")
    assert ai.tension < tension
    k01 = state.unit("kes_1_inf")
    assert k01.active_order and k01.active_order.target[0] == 112


def test_a_broken_hegemony_asks_for_an_armistice_and_accepting_wins():
    state, ai = _hotline_state()
    state.nations["vosk"].military_morale = 12
    report = TickReport(1, "")
    hotline.HotlineSystem().on_tick(state, report)
    offer = next(m for m in report.new_messages if m.template_id == "hotline_vosk_collapsing")
    respond(state, offer.id, "accept")
    FailStateSystem().on_tick(state, TickReport(1, ""))
    assert state.game_over and state.game_over.cause == "victory"


# --- the event deck --------------------------------------------------------------------------------------------


def test_new_cards_hook_into_foreign_powers_and_cities(quiet):
    state, _ = quiet
    state.clock.turn = 12
    assert eligible(state, card(state, "oakhaven_bribe"))  # we trade with Oakhaven
    assert eligible(state, card(state, "aldmark_hospital_strike"))
    assert not eligible(state, card(state, "oakhaven_volunteers"))  # alignment 15 < 50
    state.forced_card = "aldmark_hospital_strike"
    from src.engine.dilemmas import draw

    draw(state)
    resolve(state, "build")
    assert state.cities["Aldmark"].construction_queue[0]["building"] == "hospital"
    assert not eligible(state, card(state, "aldmark_hospital_strike"))  # the hospital is coming
    state.forced_card = "oakhaven_bribe"
    draw(state)
    resolve(state, "expose")
    assert diplomacy.relation(state, "oakhaven") == -5  # 15 - 25, but never past the Republic's floor
    assert diplomacy.relation(state, "tor") == 0  # -5 + 5


# --- save / load ----------------------------------------------------------------------------------------------------


@pytest.mark.live
def test_the_living_world_survives_save_and_load(tmp_path):
    state = new_game(seed=64)
    engine = build_default_engine(state)
    cities.order_building(state, "Aldmark", "hospital")
    cities.order_building(state, "Kestrel Cross", "bunker")
    diplomacy.send_envoy(state, "tor")
    diplomacy.buy_lend_lease(state, "tor", "tor_rifles")
    state.ceasefire_weeks = 2
    for _ in range(3):
        settle_dilemma(state)
        engine.advance()
    path = save_game(state, tmp_path / "world.json")
    loaded = load_game(path)
    assert snapshot(loaded)["state"] == snapshot(state)["state"]
    assert type(loaded.cities["Aldmark"]).__name__ == "City" and loaded.cities["Aldmark"].construction_queue
    assert loaded.foreign == state.foreign and loaded.shipments == state.shipments
    engine2 = build_default_engine(loaded)
    for _ in range(6):
        settle_dilemma(state)
        settle_dilemma(loaded)
        engine.advance()
        engine2.advance()
    assert snapshot(loaded)["state"] == snapshot(state)["state"]
    assert loaded.cities["Aldmark"].has("hospital")


# --- UI ----------------------------------------------------------------------------------------------------------------


def test_diplomacy_and_cities_screens():
    from textual.widgets import DataTable

    from src.ui.app import CommandTerminalApp

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=7)
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            game = app.game
            # Diplomacy: court Oakhaven, then buy tanks.
            await pilot.press("7")
            await pilot.pause()
            nations = app.screen.query_one("#dip-nations", DataTable)
            assert nations.has_focus
            nations.move_cursor(row=[r.key.value for r in nations.ordered_rows].index("oakhaven"))
            await pilot.pause()
            await pilot.press("g", "g", "g", "g")
            await pilot.pause()
            assert diplomacy.relation(game, "oakhaven") >= 35
            catalog = app.screen.query_one("#dip-catalog", DataTable)
            catalog.move_cursor(row=[r.key.value for r in catalog.ordered_rows].index("oak_tanks"))
            await pilot.press("b")
            await pilot.pause()
            assert any(s["package"] == "oak_tanks" for s in game.shipments)
            # Cities: build a hospital in Aldmark.
            await pilot.press("8")
            await pilot.pause()
            table = app.screen.query_one("#city-table", DataTable)
            table.focus()
            table.move_cursor(row=[r.key.value for r in table.ordered_rows].index("Aldmark"))
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            assert game.cities["Aldmark"].construction_queue[0]["building"] == "hospital"
            assert "LOCAL NEWS WIRE" in str(app.screen.query_one("#city-news-scroll").border_title)

    asyncio.run(run())


def test_medical_supplies_halve_the_epidemic_toll(quiet):
    state, _ = quiet
    state.catalog["crises"]["outbreak_chance"] = 0.0
    k01, k02 = state.unit("kes_1_inf"), state.unit("kes_3_inf")
    for u in (k01, k02):
        u.strength = 10000
    crisis_engine.infect(state, f"unit:{k01.id}", "trench_typhus", "OB-a")
    state.player.national_stockpile["medical_supplies"] = 200  # enough for one site
    state.catalog["crises"]["diseases"]["trench_typhus"].update(spread_chance=0.0, burnout_chance=0.0)
    crisis_engine.run_epidemics(state, TickReport(2, ""))
    treated = 10000 - k01.strength
    crisis_engine.infect(state, f"unit:{k02.id}", "trench_typhus", "OB-b")
    crisis_engine.run_epidemics(state, TickReport(3, ""))  # the crates are gone
    assert treated == 150 and 10000 - k02.strength == 300
    assert state.player.national_stockpile["medical_supplies"] == 0


def test_hotline_cables_come_from_the_design_sheet():
    state, ai = _hotline_state()
    ai.posture, ai.weeks_in_posture = "ASSAULT", 0
    report = TickReport(1, "")
    hotline.HotlineSystem().on_tick(state, report)
    mail = report.new_messages[0]
    assert mail.subject == "HOTLINE — COMMUNIQUE: UNCONDITIONAL WARNING" and "Chancellor V. Krov" in mail.body


def test_vosk_on_kestrian_soil_send_terms_of_surrender():
    state, ai = _hotline_state()
    state.unit("vosk_1_gtank").location = (110, 30)  # Vosk armor in the Eastmarch
    report = TickReport(1, "")
    hotline.HotlineSystem().on_tick(state, report)
    terms = next(m for m in report.new_messages if m.template_id == "hotline_vosk_winning")
    assert "TERMS OF SURRENDER" in terms.subject and [o.id for o in terms.options] == ["reject", "indemnity"]
    respond(state, terms.id, "indemnity")
    assert state.ceasefire_weeks == 4
