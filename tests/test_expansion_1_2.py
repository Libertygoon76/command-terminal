"""Expansion 1.2: the Royal Court & Extended Family (src/engine/court.py)."""

import asyncio

import pytest

from src.engine import court, diplomacy
from src.engine.data_loader import new_game
from src.engine.dilemmas import card, eligible, resolve
from src.engine.economy_engine import compute_ledger
from src.engine.fail_states import check_fail_state
from src.engine.savegame import load_game, save_game
from src.engine.systems import TickReport
from src.models import ASSAULT

REAL_COURT_TICK = court.CourtSystem.on_tick  # captured before conftest's calm_world stubs it out


@pytest.fixture
def state():
    return new_game(seed=12)


def house(state):
    return state.player.dynasty


def tick(state):
    report = TickReport(state.clock.turn, "")
    REAL_COURT_TICK(court.CourtSystem(), state, report)
    return report


# --- the starting court -----------------------------------------------------------------------------


def test_the_house_is_seated_from_the_writers_cast(state):
    h = house(state)
    assert h.ruler.name == "Valerius" and h.ruler.traits == ["pragmatic", "war_weary"]
    cast = {c.id: c for c in h.characters.values()}
    assert cast["char_02"].relation == "Heir" and cast["char_02"].traits == ["glory_hound", "inexperienced"]
    assert cast["char_03"].traits == ["corrupt", "financial_genius"] and cast["char_03"].loyalty == 35
    relatives = [c for c in h.living() if c.relation not in ("Ruler", "Noble")]
    nobles = [c for c in h.living() if c.relation == "Noble"]
    assert 5 <= len(relatives) <= 8 and 2 <= len(nobles) <= 3
    assert {c.office for c in h.living() if c.office} == {"finance", "war", "intelligence"}


def test_the_genius_and_the_incompetent(state):
    """The Duke is a Financial Genius but corrupt and disloyal; Julian is devoted and hopeless with money."""
    base = compute_ledger(state)
    tax = next(v for k, v in base.income.items() if k.startswith("Tax revenue"))
    bonus = next(v for k, v in base.income.items() if k.startswith("Minister of Finance"))
    skim = base.expenses["Unaccounted expenditure (Treasury)"]
    assert bonus == round(tax * 0.03 * (9 + 3 - 5)) and skim == round(tax * 0.04)
    court.appoint(state, "char_02", "finance")  # the loyal, incompetent heir takes over
    after = compute_ledger(state)
    assert after.expenses["Minister of Finance: Julian (ADM 3)"] == round(tax * 0.03 * 2)  # ADM 3 < 5: a loss
    assert "Unaccounted expenditure (Treasury)" not in after.expenses
    assert house(state).characters["char_03"].loyalty == 35 - 15  # dismissed, and he resents it


def test_the_ruler_cannot_serve_and_the_dead_cannot_be_appointed(state):
    with pytest.raises(court.CourtError):
        court.appoint(state, "valerius", "war")
    court.die(state, house(state).characters["char_04"], "died of old age")
    with pytest.raises(court.CourtError):
        court.appoint(state, "char_04", "war")


def test_loyalty_drifts_toward_its_target(state):
    vargus = house(state).characters["char_03"]
    target = court.loyalty_target(state, vargus)
    tick(state)
    assert vargus.loyalty == pytest.approx(35 + (target - 35) * 0.05, abs=0.01)


# --- holding court ------------------------------------------------------------------------------------


def test_holding_court_grants_an_audience(state):
    audience = court.hold_court(state)
    assert state.pending_dilemma == audience["id"] and audience["category"] == "ROYAL COURT"
    petitioner = house(state).characters[audience["audience"]]
    before = petitioner.loyalty
    grant = audience["choices"][0]["effects"]["court"]["loyalty"][petitioner.id]
    resolve(state, "grant")
    assert petitioner.loyalty == pytest.approx(min(100, before + grant))
    with pytest.raises(court.CourtError):
        court.hold_court(state)  # once every two weeks


def test_denying_and_ignoring_cost_loyalty(state):
    elara = house(state).characters["char_01"]
    for choice, cost in (("deny", -8), ("ignore", -4)):
        entry = court.audience_card(state, elara)
        assert entry["title"].endswith("Pleads for Peace")  # the Pacifist asks for peace talks
        court._raise(state, entry)
        before = elara.loyalty
        resolve(state, choice)
        assert elara.loyalty == pytest.approx(before + cost)


def test_petitioners_demand_audiences_on_their_own(state):
    h = house(state)
    state.clock.turn = h.next_audience_turn
    report = tick(state)
    assert state.pending_dilemma and state.pending_dilemma.startswith("audience_")
    assert h.next_audience_turn > state.clock.turn and report.dilemma == state.pending_dilemma


# --- treason ------------------------------------------------------------------------------------------


def test_disloyal_courtiers_plot_and_embezzle(state):
    state.catalog["court"]["treason"]["plot_chance"] = 1.0
    state.catalog["court"]["cabinet"]["intelligence"]["vacant_detection"] = 0.0
    for c in house(state).living():
        c.office = None  # no Head of Intelligence to catch anyone
    vargus = house(state).characters["char_03"]
    vargus.loyalty = 5
    state.catalog["court"]["loyalty"]["drift"] = 0.0
    state.catalog["court"]["treason"]["plots"] = {"embezzle": {"weight": 1, "amount": [20000, 20000]}}
    treasury = state.player.treasury
    tick(state)
    assert state.player.treasury <= treasury - 20000
    assert any("FUNDS MISSING" in m.subject for m in state.inbox.newest_first())


def test_the_head_of_intelligence_uncovers_plots(state):
    state.catalog["court"]["treason"]["plot_chance"] = 1.0
    state.catalog["court"]["cabinet"]["intelligence"]["base_detection"] = 1.0
    state.catalog["court"]["loyalty"]["drift"] = 0.0
    vargus = house(state).characters["char_03"]
    vargus.loyalty = 5
    tick(state)
    treason = card(state, state.pending_dilemma)
    assert treason["category"] == "INTERNAL SECURITY" and treason["title"] == "Treason: Duke Vargus"
    resolve(state, "execute")
    assert not vargus.alive and house(state).minister("finance") is None


def test_a_successful_coup_is_protocol_zero(state):
    h = house(state)
    rebel = h.characters["char_04"]
    rebel.traits.append("ambitious")
    rebel.influence, rebel.loyalty = 90, 0
    state.rng.seed(1)
    state.catalog["court"]["treason"]["plots"]["coup"]["base_success"] = 5.0
    court.run_plot(state, rebel, "coup", TickReport(1, ""))
    assert check_fail_state(state) == "dynastic_collapse"
    assert state.catalog["alerts"]["dynastic_collapse"]["subject"].endswith("PROTOCOL ZERO — DYNASTIC COLLAPSE")


# --- marriage -----------------------------------------------------------------------------------------


def test_a_royal_marriage_binds_a_power(state):
    julian = house(state).characters["char_02"]
    entry = court.marriage_card(state, julian.id)
    assert [c["id"] for c in entry["choices"]][-1] == "not_yet"
    before = diplomacy.relation(state, "vael")
    resolve(state, "marry_vael")
    assert julian.married_to == "vael" and diplomacy.relation(state, "vael") == pytest.approx(min(50, before + 20))
    diplomacy.adjust_relation(state, "vael", state.player.id, -80)
    assert diplomacy.relation(state, "vael") == 20  # never below +20 while the marriage holds
    assert court.lend_lease_price(state, "vael", 20000) == 15000
    assert court.lend_lease_price(state, "vael", 20000, "vosk") == 20000
    with pytest.raises(court.CourtError):
        court.marriage_card(state, julian.id)  # already married


# --- family generals ----------------------------------------------------------------------------------


def test_royal_generals_never_disobey_but_can_die(state):
    from src.engine.command import refusal_chance

    silas = house(state).characters["char_04"]
    unit = court.frontline_unit(state)
    court.appoint_commander(state, silas.id, unit.id)
    assert silas.office is None and unit.commander == "General Silas" and "royal" in unit.traits
    assert "aggressive" in unit.traits  # Ruthless in the field
    unit.stance = ASSAULT
    assert refusal_chance(state, unit, ASSAULT) == (0.0, "")
    stability = house(state).stability
    state.remove_unit(unit)  # the division is destroyed
    tick(state)
    assert not silas.alive and "killed in action" in silas.died
    assert house(state).stability <= stability - 20 + 1


def test_the_writers_heir_chain_puts_julian_at_the_front_and_can_kill_him(state):
    julian = house(state).characters["char_02"]
    assert eligible(state, card(state, "gemini_julian_glory"))
    state.pending_dilemma = "gemini_julian_glory"
    resolve(state, "option_1")
    assert julian.unit_id is not None
    state.pending_dilemma = "gemini_julian_dead"
    resolve(state, "option_1")
    assert not julian.alive and house(state).characters["sibling_1"].relation == "Heir"
    assert not eligible(state, card(state, "gemini_julian_glory"))


def test_executing_the_duke_through_the_writers_card(state):
    state.pending_dilemma = "gemini_uncle_embezzlement"
    resolve(state, "option_1")
    assert not house(state).characters["char_03"].alive
    assert state.card_schedule[0]["card"] == "gemini_vargus_revenge"


# --- succession ---------------------------------------------------------------------------------------


def test_the_heir_succeeds_and_inherits_the_mess(state):
    h = house(state)
    court.die(state, h.ruler, "assassinated")
    assert h.ruler.name == "Julian" and h.ruler.traits == ["glory_hound", "inexperienced"]
    assert h.characters["char_01"].relation == "Dowager"
    assert h.stability == 40 and any(c.relation == "Heir" for c in h.living())
    assert any("LONG LIVE THE LORD PROTECTOR JULIAN" in m.subject for m in state.inbox.newest_first())


def test_no_one_left_of_the_blood_is_protocol_zero(state):
    h = house(state)
    for char in list(h.blood()):
        if char.relation != "Ruler":
            court.die(state, char, "died of the fever")
    assert not state.flags.get("dynastic_collapse")
    court.die(state, h.ruler, "died of old age")
    assert check_fail_state(state) == "dynastic_collapse"


def test_the_plague_in_the_capital_reaches_the_palace(state):
    from src.engine import crisis_engine

    crisis_engine.infect(state, "city:Aldmark", "cholera", "OB-t")
    state.catalog["court"]["health"].update(plague_chance=1.0, ill_death_chance=0.0, age_base=0.0)
    tick(state)
    assert house(state).ruler.ill_weeks == 4


# --- persistence and the screen -----------------------------------------------------------------------


def test_the_court_survives_save_and_load(state, tmp_path):
    h = house(state)
    court.appoint(state, "char_02", "war")
    court.die(state, h.characters["char_03"], "executed for treason")
    h.stability = 33.5
    path = save_game(state, tmp_path / "save.json")
    loaded = load_game(path).player.dynasty
    assert loaded == h
    assert loaded.characters["char_02"].office == "war" and not loaded.characters["char_03"].alive


def test_royal_court_screen_holds_an_audience():
    from src.ui.app import CommandTerminalApp
    from src.ui.screens.dilemma import DilemmaScreen
    from src.ui.views.court import CourtView

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=1984)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("0")
            await pilot.pause()
            view = app.screen.query_one(CourtView)
            assert view.display and view.query_one("#court-table").row_count >= 8
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, DilemmaScreen)
            await pilot.press("1")
            await pilot.pause()
            assert not app.game.pending_dilemma
            house = app.game.player.dynasty
            assert house.last_court_turn == app.game.clock.turn

    asyncio.run(run())
