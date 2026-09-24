"""Phase 7: continental scale, naval warfare & blockades, combined arms, weather, and the event deck."""

import asyncio

import pytest

from src.engine import dilemmas
from src.engine.air_engine import AirSystem, air_bonus, assign_wing, cycle_wing, wing
from src.engine.combat_engine import CombatSystem, firepower, fight_round, _battle_for
from src.engine.data_loader import new_game
from src.engine.dilemmas import DilemmaError, resolve
from src.engine.economy_engine import compute_ledger, run_economy
from src.engine.logistics_engine import burn_fuel, establishment, speed_factor, supply_sources, update_supply
from src.engine.movement import OrderError, _engage, issue_move_order, plan_route, resolve_movement, speed_of
from src.engine.naval_engine import MissionError, NavalSystem, ai_naval_orders, set_mission, ships_left
from src.engine.production import forecast
from src.engine.recruitment import muster_point, raise_formation
from src.engine.research import ResearchSystem, start_research, weeks_remaining
from src.engine.systems import TickReport
from src.engine.tick_engine import MILES_PER_CELL, DilemmaPendingError, build_default_engine, cells_per_turn
from src.engine.weather_engine import FrostSystem, apply_frost, season_of, winter_kitted
from src.models import MOVING
from src.models.ai import PROBE
from tests.conftest import settle_dilemma


@pytest.fixture
def quiet():
    state = new_game(seed=77)
    state.ai_states.clear()
    return state, build_default_engine(state)


def ships(unit):
    return sum(unit.equipment_inventory.get(k, 0) for k in ("destroyer", "battleship", "submarine"))


def set_weather(state, condition, storm=False):
    state.weather = {"condition": condition, "season": "Winter", "storm": storm, "turn": state.clock.turn}


# --- 1. scale and geography ---------------------------------------------------------------------


def test_continental_map_with_oceans_ports_and_towns(quiet):
    state, _ = quiet
    world = state.world_map
    assert (world.width, world.height) == (240, 80)  # 1,200 x 800 miles
    assert MILES_PER_CELL == 10 and state.config["map"]["miles_per_cell"] == 10
    sea = sum(world.is_sea(x, y) for y in range(world.height) for x in range(world.width))
    assert sea / (world.width * world.height) > 0.3  # a real ocean, on three sides
    kinds = [f.type for f in world.features]
    assert kinds.count("town") >= 24 and kinds.count("industrial") >= 4 and kinds.count("port") >= 9
    for port in world.ports():
        assert world.is_coastal(port.x, port.y) and world.is_sea(*port.harbour)
    owners = {world.owner_at(p.x, p.y) for p in world.ports()}
    assert {"kestria", "vosk"} <= owners


def test_marching_speed_is_miles_per_day(quiet):
    state, _ = quiet
    inf = state.unit("kes_1_inf")
    assert speed_of(state, inf) == pytest.approx(cells_per_turn(state, 7))  # 4 mi/day on foot + 3 by truck
    inf.equipment_inventory["truck_4t"] = 0
    on_foot = speed_of(state, inf)
    assert on_foot == pytest.approx(2.8) and on_foot < 5  # 28 miles a week: not 50 in a few days
    assert speed_of(state, state.unit("kes_7_arm")) == pytest.approx(8.4)
    assert speed_of(state, state.unit("kes_1_dd")) > 40  # warships cover hundreds of miles a week


# --- 2. navies ------------------------------------------------------------------------------------


def test_warships_sail_only_on_open_water(quiet):
    state, engine = quiet
    dd = state.unit("kes_1_dd")
    with pytest.raises(OrderError):
        issue_move_order(state, dd.id, (110, 20))  # land
    with pytest.raises(OrderError):
        issue_move_order(state, "kes_1_inf", (121, 14))  # infantry into the sea
    route = issue_move_order(state, dd.id, (121, 14))
    assert all(state.world_map.is_sea(*c) for c in route.path)
    for _ in range(route.eta_weeks + 1):
        engine.advance()
        assert state.world_map.is_sea(*dd.location)
    assert dd.location == (121, 14)


def test_fleets_and_armies_never_engage_each_other(quiet):
    state, _ = quiet
    k01 = state.unit("kes_1_inf")
    k01.location = (121, 16)
    vn1 = state.unit("vosk_north_dd")
    vn1.location = (121, 13)
    issue_move_order(state, vn1.id, (121, 15), nation_id="vosk")  # sails right up to our coast
    resolve_movement(state)
    assert not k01.engaged and not vn1.engaged


def test_blockade_closes_a_port_cuts_trade_and_supply(quiet):
    state, _ = quiet
    before = compute_ledger(state).total_income
    raider = state.unit("vosk_north_dd")
    raider.location = (45, 8)  # off Northwatch
    report = TickReport(2, "")
    NavalSystem().on_tick(state, report)
    assert "Northwatch" not in state.blockades  # on PATROL it does not blockade
    set_mission(state, raider.id, "blockade", nation_id="vosk")
    NavalSystem().on_tick(state, report)
    assert state.blockades == {"Northwatch": "vosk"}
    assert compute_ledger(state).total_income < before  # 1,000 CR/week of overseas trade lost (x productivity)
    northwatch = state.world_map.feature_named("Northwatch")
    assert (northwatch.x, northwatch.y) not in supply_sources(state, "kestria")
    assert any("NAVAL BLOCKADE: NORTHWATCH" in m.subject for m in report.new_messages)
    # A Kestrian squadron contesting the waters lifts it.
    state.unit("kes_1_dd").location = (46, 8)
    NavalSystem().on_tick(state, report)
    assert not state.blockades
    assert any("BLOCKADE LIFTED" in m.subject for m in report.new_messages)


def test_naval_battle_burns_shells_and_torpedoes_and_sinks_ships(quiet):
    state, _ = quiet
    subs, dds = state.unit("kes_2_sub"), state.unit("vosk_north_dd")
    subs.location, dds.location = (60, 70), (62, 70)
    _engage(state, subs, dds, [])
    torpedoes, shells = subs.equipment_inventory["torpedoes"], dds.equipment_inventory["naval_shells"]
    fleet = ships(subs) + ships(dds)
    report = TickReport(2, "")
    for _ in range(4):
        CombatSystem().on_tick(state, report)
        if not state.engagements:
            break
    assert shells > state.unit("vosk_north_dd").equipment_inventory["naval_shells"] if state.unit("vosk_north_dd") else True
    battle = next(iter(state.battles.values()))
    assert battle.ammo_expended["kestria"]["torpedoes"] > 0 and battle.ammo_expended["vosk"]["naval_shells"] > 0
    survivors = sum(ships(u) for u in (state.unit("kes_2_sub"), state.unit("vosk_north_dd")) if u)
    assert survivors < fleet  # ships went down
    assert "Battle of" in battle.name and "Sea" in battle.name or "Straits" in battle.name
    assert any(m.template_id in ("sitrep", "after_action_report") for m in report.new_messages)
    assert torpedoes >= 0


def _coastal_battle(bombard: bool):
    state = new_game(seed=5)
    state.ai_states.clear()
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    ours.location, theirs.location = (121, 16), (123, 16)
    fleet = state.unit("kes_home_fleet")
    fleet.location = (121, 14)
    if bombard:
        set_mission(state, fleet.id, "bombard")
    _engage(state, ours, theirs, [])
    shells = fleet.equipment_inventory["naval_shells"]
    before = theirs.strength
    state.rng.seed(3)
    result = fight_round(state, _battle_for(state, {ours.id, theirs.id}), {ours.id, theirs.id})
    return before - theirs.strength, shells - fleet.equipment_inventory["naval_shells"], result


def test_offshore_bombardment_supports_coastal_battles():
    without, spent_without, _ = _coastal_battle(False)
    with_guns, spent, result = _coastal_battle(True)
    assert spent_without == 0 and spent > 0  # only a BOMBARD mission fires
    assert with_guns > without * 1.5  # battleship guns are massive
    assert any("naval gunfire" in line for line in result.support["kestria"])


def test_artillery_behind_the_line_fires_in_support_without_losses():
    def battle(guns_in_range: bool):
        state = new_game(seed=6)
        state.ai_states.clear()
        ours, theirs, guns = state.unit("kes_3_inf"), state.unit("vosk_9_rifle"), state.unit("kes_12_art")
        theirs.location = (123, 29)  # in contact with K-02 at (121, 29); K-06 sits 3 columns behind it
        if not guns_in_range:
            guns.location = (104, 36)
        _engage(state, ours, theirs, [])
        shells, men = guns.equipment_inventory["shell_152_he"], guns.strength
        before = theirs.strength
        state.rng.seed(1)
        result = fight_round(state, _battle_for(state, {ours.id, theirs.id}), {ours.id, theirs.id})
        return before - theirs.strength, shells - guns.equipment_inventory["shell_152_he"], guns.strength - men, result

    with_guns, spent, lost, result = battle(True)
    without, spent_none, _, _ = battle(False)
    assert spent >= 1000 and spent_none == 0  # thousands of shells a week
    assert lost == 0  # the guns are not in contact: no casualties
    assert with_guns > without * 1.4
    assert any("rounds fired in support" in line for line in result.support["kestria"])


# --- 3. air power ----------------------------------------------------------------------------------


def test_air_superiority_buffs_battles_and_burns_fuel_and_bombs(quiet):
    state, _ = quiet
    for w in state.air_wings:
        w.sector = "frontier" if w.nation_id == "kestria" else None
    ours, theirs = state.unit("kes_1_inf"), state.unit("vosk_4_rifle")
    theirs.location = (123, 20)
    _engage(state, ours, theirs, [])
    fuel = state.player.national_stockpile["aviation_fuel"]
    bombs = state.player.national_stockpile["bombs"]
    AirSystem().on_tick(state, TickReport(2, ""))
    assert all(w.status == "SUPERIORITY" for w in state.air_wings if w.nation_id == "kestria")
    assert state.player.national_stockpile["aviation_fuel"] < fuel and state.player.national_stockpile["bombs"] < bombs
    assert air_bonus(state, "kestria", ours.location) == pytest.approx(1.3)
    assert air_bonus(state, "vosk", ours.location) == 1.0
    # Contested skies: an equal enemy force denies the bonus.
    for w in state.air_wings:
        w.sector = "frontier"
    AirSystem().on_tick(state, TickReport(3, ""))
    assert air_bonus(state, "kestria", ours.location) == 1.0
    # A blizzard grounds everyone.
    set_weather(state, "blizzard")
    AirSystem().on_tick(state, TickReport(4, ""))
    assert all(w.status == "GROUNDED" for w in state.air_wings if w.sector)


def test_wing_assignment_rules(quiet):
    state, _ = quiet
    w = assign_wing(state, "kes_1_taw", "eastmarch")
    assert w.sector == "eastmarch"
    assert cycle_wing(state, "kes_1_taw", 1).sector != "eastmarch"
    assign_wing(state, "kes_1_taw", None)
    assert wing(state, "kes_1_taw").sector is None
    with pytest.raises(Exception):
        assign_wing(state, "vosk_1_gar", "frontier")  # not ours


# --- 4. weather ------------------------------------------------------------------------------------


def test_seasons_follow_the_calendar(quiet):
    state, _ = quiet
    assert [season_of(state, m) for m in (1, 4, 7, 10)] == ["Winter", "Spring", "Summer", "Autumn"]


def test_mud_season_slows_everything_and_doubles_armor_fuel(quiet):
    state, _ = quiet
    tank = state.unit("kes_7_arm")
    set_weather(state, "clear")
    dry = speed_factor(state, tank)
    tank.status = MOVING
    fuel = tank.equipment_inventory["fuel_drums"]
    burn_fuel(state, tank)
    dry_burn = fuel - tank.equipment_inventory["fuel_drums"]
    set_weather(state, "mud")
    assert speed_factor(state, tank) == pytest.approx(dry * 0.45)
    fuel = tank.equipment_inventory["fuel_drums"]
    burn_fuel(state, tank)
    assert fuel - tank.equipment_inventory["fuel_drums"] == 2 * dry_burn


def test_winter_freezes_men_without_winter_gear(quiet):
    state, _ = quiet
    set_weather(state, "blizzard")
    ours = state.unit("kes_1_inf")
    vosk = state.unit("vosk_4_rifle")
    assert not winter_kitted(state, ours) and winter_kitted(state, vosk)  # the Vosk came dressed for it
    ours.status, ours.supply_state = MOVING, "supplied"
    men, their_men, morale = ours.strength, vosk.strength, ours.morale
    apply_frost(state)
    assert ours.strength < men * 0.98 and ours.morale < morale
    assert vosk.strength == their_men
    # Winter quarters (holding inside the supply net) cut the losses.
    exposed = men - ours.strength
    ours.status, ours.strength = "holding", men
    apply_frost(state)
    assert men - ours.strength < exposed / 2
    # Cold-Weather Equipment research gives kit to every man.
    start_research(state, state.player.id, "winter_warfare")
    for _ in range(int(weeks_remaining(state, state.player, "winter_warfare"))):
        ResearchSystem().on_tick(state, TickReport(2, ""))
    assert establishment(state, ours)["winter_gear"] >= ours.strength * 0.99


def test_winter_cuts_the_firepower_of_unkitted_troops(quiet):
    state, _ = quiet
    from src.engine.weather_engine import combat_factor

    set_weather(state, "snow")
    assert combat_factor(state, state.unit("kes_1_inf")) == pytest.approx(0.75)
    assert combat_factor(state, state.unit("vosk_4_rifle")) == pytest.approx(0.95)
    set_weather(state, "clear")
    assert combat_factor(state, state.unit("kes_1_inf")) == 1.0


def test_storms_batter_ships_at_sea_but_not_in_port(quiet):
    state, _ = quiet
    set_weather(state, "snow", storm=True)
    at_sea, in_port = state.unit("kes_1_dd"), state.unit("kes_home_fleet")
    at_sea.location = (60, 70)
    men_sea, men_port = at_sea.strength, in_port.strength
    NavalSystem().on_tick(state, TickReport(2, ""))
    assert at_sea.strength < men_sea and in_port.strength == men_port


@pytest.mark.live
def test_weather_rolls_every_week_and_opens_with_a_winter_bulletin():
    state = new_game(seed=12)
    assert state.weather["season"] == "Winter"
    assert any(m.template_id == "weather_bulletin" for m in state.inbox.messages)
    engine = build_default_engine(state)
    seen = set()
    for _ in range(16):
        settle_dilemma(state)
        engine.advance()
        seen.add(state.weather["condition"])
    assert seen & {"snow", "blizzard"} and "mud" in seen  # winter, then the spring Rasputitsa


# --- 5. the event deck -------------------------------------------------------------------------------


def test_dilemma_pauses_the_game_until_answered(quiet):
    state, engine = quiet
    state.forced_card = "worker_strike"
    assert dilemmas.draw(state) == "worker_strike"
    with pytest.raises(DilemmaPendingError):
        engine.advance()
    with pytest.raises(DilemmaError):
        resolve(state, "nonsense")
    morale = state.player.morale
    output = forecast(state, state.player, "ammo_762")
    changes = resolve(state, "crush")
    assert state.player.morale == pytest.approx(morale - 6)
    assert forecast(state, state.player, "ammo_762") == pytest.approx(output * 1.15)
    assert any("FACTORY OUTPUT +15% FOR 4 WEEKS" in c for c in changes)
    assert any(m.subject.startswith("DECISION RECORDED") for m in state.inbox.messages)
    for _ in range(4):
        run_economy(state, state.player)  # the martial-law quotas wear off
    assert forecast(state, state.player, "ammo_762") == pytest.approx(output)
    engine.advance()  # unblocked


@pytest.mark.live
def test_cards_are_drawn_by_chance_with_conditions():
    state = new_game(seed=3)
    state.catalog["events_deck"]["draw_chance"] = 1.0
    state.clock.turn = 5
    drawn = dilemmas.draw(state)
    assert drawn and state.pending_dilemma == drawn
    card = dilemmas.card(state, drawn)
    assert "Summer" not in card.get("conditions", {}).get("seasons", ["Winter"]) or False
    assert dilemmas.draw(state) is None  # one at a time
    resolve(state, card["choices"][-1]["id"])
    assert dilemmas.draw(state) is None  # min_weeks_between
    # Winter-only cards never come up in summer, research cards need a project.
    state.clock.turn = 30  # late July
    assert not dilemmas.eligible(state, dilemmas.card(state, "frozen_convoy"))
    assert not dilemmas.eligible(state, dilemmas.card(state, "crash_program"))


def test_every_card_has_two_or_three_valid_choices(quiet):
    state, _ = quiet
    deck = dilemmas.cards(state)
    assert len(deck) >= 12
    for entry in deck.values():
        assert 2 <= len(entry["choices"]) <= 3


# --- 6. 5.56mm modernization ------------------------------------------------------------------------


def test_intermediate_cartridge_rearms_infantry_with_5_56(quiet):
    state, _ = quiet
    nation = state.player
    k01 = state.unit("kes_1_inf")
    old_fire = firepower(state, k01, consume=False)
    start_research(state, nation.id, "intermediate_cartridge")
    for _ in range(int(weeks_remaining(state, nation, "intermediate_cartridge"))):
        ResearchSystem().on_tick(state, TickReport(2, ""))
    wanted = establishment(state, k01)
    assert wanted["rifle_556"] > 0 and "rifle_762" not in wanted
    # Re-arming: 5.56mm rifles arrive, 7.62mm rifles go back to the depots one-for-one.
    nation.national_stockpile.update(rifle_556=60000, ammo_556=60000)  # enough for all four divisions
    armed = k01.equipment_inventory["rifle_762"]
    depot_762 = nation.national_stockpile["rifle_762"]
    for _ in range(12):
        update_supply(state)
    inv = k01.equipment_inventory
    assert inv["rifle_556"] > armed * 0.8 and inv.get("rifle_762", 0) < armed * 0.2
    assert nation.national_stockpile["rifle_762"] > depot_762
    assert inv["rifle_556"] + inv.get("rifle_762", 0) <= establishment(state, k01)["rifle_556"] + 1
    assert establishment(state, k01)["ammo_556"] > 2400 * 0.9  # 30% more rounds per man
    new_fire = firepower(state, k01, consume=False)
    assert new_fire.soft > old_fire.soft * 1.1  # the combat buff


# --- 7. recruitment & AI at sea ------------------------------------------------------------------------


def test_new_warships_muster_at_the_busiest_harbour(quiet):
    state, engine = quiet
    state.player.treasury = 5_000_000
    template = state.template("destroyer_flotilla")
    assert muster_point(state, state.player, template) == state.world_map.feature_named("Port Cassel").harbour
    order = raise_formation(state, state.player.id, "destroyer_flotilla")
    for _ in range(order.weeks_total):
        engine.advance()
    ship = state.unit(order.id)
    assert ship is not None and state.world_map.is_sea(*ship.location)


def test_vosk_admiralty_blockades_in_probe_posture():
    state = new_game(seed=4)
    ai = state.ai_states["vosk"]
    ai.posture = PROBE
    orders = ai_naval_orders(state, ai)
    assert any(mission == "blockade" for _, _, mission in orders)
    assert all(state.world_map.is_sea(*target) for _, target, _ in orders)


def test_mission_rules(quiet):
    state, _ = quiet
    with pytest.raises(MissionError):
        set_mission(state, "kes_1_inf", "blockade")  # not a warship
    with pytest.raises(MissionError):
        set_mission(state, "vosk_north_dd", "blockade")  # not ours
    set_mission(state, "kes_1_dd", "bombard")
    assert state.unit("kes_1_dd").mission == "bombard"


# --- UI -----------------------------------------------------------------------------------------------


@pytest.mark.live
def test_dilemma_modal_and_air_and_naval_keys():
    from textual.widgets import DataTable

    from src.ui.app import CommandTerminalApp
    from src.ui.screens.dilemma import DilemmaScreen
    from src.ui.widgets.map_canvas import MapCanvas

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=12, event="worker_strike")
        async with app.run_test(size=(170, 50)) as pilot:
            await pilot.pause()
            game = app.game
            treasury = game.player.treasury
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, DilemmaScreen) and game.pending_dilemma == "worker_strike"
            assert "STRIKE AT THE IRONVALE STEELWORKS" in str(app.screen.query_one("#dilemma-title").render())
            week = game.clock.turn
            await pilot.press("n")  # nothing advances behind the modal
            await pilot.pause()
            assert game.clock.turn == week
            await pilot.press("2")  # concede
            await pilot.pause()
            assert not isinstance(app.screen, DilemmaScreen) and game.pending_dilemma is None
            assert game.player.treasury < treasury

            # Air Assets: send the first wing forward.
            await pilot.press("6")
            await pilot.pause()
            table = app.screen.query_one("#air-wings", DataTable)
            assert table.has_focus and table.row_count == 3
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert game.air_wings[0].sector is not None
            await pilot.press("0")
            await pilot.pause()
            assert game.air_wings[0].sector is None

            # War Room: T cycles a warship's mission.
            await pilot.press("4")
            await pilot.pause()
            canvas = app.screen.query_one(MapCanvas)
            dd = game.unit("kes_1_dd")
            canvas.jump_to(*dd.location)
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            assert dd.mission == "blockade"
            assert "BLOCKADE" in str(app.screen.query_one("#intel-readout").render())

    asyncio.run(run())
