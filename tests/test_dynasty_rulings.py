"""The Game Director's rulings on the dynasty (src/engine/court.py): the Regency for an under-age ruler, and royal
births as narrative and morale events only."""

import asyncio
import sys
from pathlib import Path

import pytest

from src.engine import court, diplomacy
from src.engine.cities import order_building
from src.engine.data_loader import new_game
from src.engine.economy_engine import compute_ledger
from src.engine.effects import apply_effects, preview_effects
from src.engine.recruitment import raise_formation
from src.engine.savegame import load_game, save_game
from src.engine.systems import TickReport

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


def crown_a_child(state, age=12):
    """Julian (the Heir) is made a boy of `age`, and the Lord Protector dies."""
    h = house(state)
    h.characters["char_02"].age = age
    court.die(state, h.ruler, "assassinated")
    return h


# --- the regency ----------------------------------------------------------------------------------------


def test_an_under_age_heir_reigns_through_the_ablest_minister(state):
    h = crown_a_child(state)
    assert h.ruler.id == "char_02" and court.regency_active(state)
    ablest = max((c for c in h.living() if c.office), key=lambda c: c.administration)
    assert ablest.id == "char_03"  # Duke Vargus, ADM 9, Minister of Finance
    assert h.regent_id == "char_03" and h.regent is ablest and h.regency_since == state.clock.turn
    succession = next(m for m in state.inbox.newest_first() if "LONG LIVE" in m.subject)
    assert "REGENCY is proclaimed" in succession.body and "Duke Vargus" in succession.body
    assert any("Regency proclaimed" in e["text"] for e in h.history)


def test_an_adult_heir_needs_no_regent(state):
    court.die(state, house(state).ruler, "assassinated")  # Julian is 22
    assert not court.regency_active(state) and house(state).regent is None


def test_a_regency_makes_every_weekly_cost_ten_percent_dearer(state):
    before = compute_ledger(state)
    crown_a_child(state)
    after = compute_ledger(state)
    label = "Regency: waste and embezzlement (+10%)"
    assert label not in before.expenses and label in after.expenses
    others = sum(v for k, v in after.expenses.items() if k != label)
    assert after.expenses[label] == round(others * 0.10)


def test_a_regency_makes_every_purchase_ten_percent_dearer(state):
    state.player.treasury = 5_000_000
    crown_a_child(state)
    template = next(t for t in state.catalog["units"]["units"] if t["id"] == "infantry_division")
    treasury = state.player.treasury
    raise_formation(state, state.player.id, "infantry_division")
    assert treasury - state.player.treasury == round(template["recruit_cost"] * 1.1)
    treasury = state.player.treasury
    project = order_building(state, "Aldmark", "bunker")
    assert project["cost"] == round(45000 * 1.1) and treasury - state.player.treasury == project["cost"]
    treasury = state.player.treasury
    diplomacy.send_envoy(state, "vael")
    assert treasury - state.player.treasury == round(25000 * 1.1)
    assert court.regency_price(state, state.player, 100_000) == 110_000
    assert court.regency_price(state, "vosk", 100_000) == 100_000  # the Vosk have no regency


def test_the_regent_is_harder_to_keep_loyal(state):
    h = crown_a_child(state)
    regent = h.regent
    target = court.loyalty_target(state, regent)
    h.regent_id = ""
    assert court.loyalty_target(state, regent) == pytest.approx(min(100.0, target + 10))


def test_a_disloyal_regent_may_seize_the_palace_whatever_their_blood(state):
    h = crown_a_child(state)
    noble = next(c for c in h.living() if c.relation == "Noble")
    noble.traits, noble.influence, noble.loyalty = [], 30.0, 5.0
    state.catalog["court"]["treason"]["plots"] = {"coup": {"weight": 1, "min_influence": 60, "base_success": 0.25}}
    assert court._plot_type(state, noble) is None  # a loyal-born, unambitious noble would never try
    h.regent_id = noble.id
    assert court._plot_type(state, noble) == "coup"


def test_a_fallen_regent_is_replaced_by_the_next_ablest_minister(state):
    h = crown_a_child(state)
    court.die(state, h.characters["char_03"], "executed for treason")
    expected = max((c for c in h.living() if c.office and c.relation != "Ruler"),
                   key=lambda c: (c.administration, c.loyalty))
    assert h.regent is expected and court.regency_active(state)
    assert any(m.subject.startswith("THE REGENCY PASSES TO") for m in state.inbox.newest_first())
    court.imprison(state, expected)
    assert h.regent is not expected and h.regent is not None


def test_with_no_cabinet_the_ablest_courtier_at_court_is_regent(state):
    h = house(state)
    for c in h.living():
        c.office = None
    crown_a_child(state)
    at_court = [c for c in h.living() if c.relation != "Ruler" and c.at_court]
    assert h.regent is min(at_court, key=lambda c: (-c.administration, -c.loyalty, c.id))


def test_with_nobody_fit_the_clerks_govern_without_a_weekly_dispatch(state):
    h = crown_a_child(state)
    state.catalog["court"]["health"]["age_base"] = 0.0
    for c in h.living():
        if c.relation != "Ruler":
            c.imprisoned = True
    court.replace_regent(state)
    assert h.regent_id == "" and court.regency_active(state)
    mail = len(state.inbox.newest_first())
    tick(state)
    tick(state)
    assert not any(m.subject.startswith("THE REGENCY PASSES") for m in state.inbox.newest_first()[:len(
        state.inbox.newest_first()) - mail])


def test_the_regent_cannot_leave_aldmark(state):
    h = crown_a_child(state)
    regent = h.characters["char_03"]
    with pytest.raises(court.CourtError, match="Regent"):
        court.appoint_commander(state, regent.id, state.player.units[0].id)
    assert regent not in court.marriage_candidates(state)


def test_the_regency_ends_when_the_ruler_comes_of_age(state):
    h = crown_a_child(state, age=17)
    state.catalog["court"]["health"]["age_base"] = 0.0
    tick(state)
    assert court.regency_active(state)
    h.ruler.age = 18
    tick(state)
    assert not court.regency_active(state) and h.regent_id == ""
    assert "Regency: waste and embezzlement (+10%)" not in compute_ledger(state).expenses
    assert any(m.subject.startswith("THE REGENCY ENDS") for m in state.inbox.newest_first())


def test_a_child_ruler_succeeded_by_an_adult_ends_the_regency(state):
    h = crown_a_child(state)
    for c in h.living():
        if c.dynasty and c.relation not in ("Ruler",):
            c.age = max(c.age, 30)
    court.die(state, h.ruler, "died of the fever")
    assert h.ruler.age >= 18 and not court.regency_active(state)


# --- royal births -------------------------------------------------------------------------------------


def test_a_royal_birth_lifts_the_nation_but_adds_no_courtier(state):
    h = house(state)
    spec = state.catalog["court"]["births"]
    spec.update(chance_per_week=1.0, first_turn=1)
    people, morale, stability = len(h.characters), state.player.morale, h.stability
    tick(state)
    assert h.births == 1 and h.last_birth_turn == state.clock.turn
    assert len(h.characters) == people  # narrative only: the child never holds court
    assert state.player.morale >= morale + spec["civil_morale"] - 1  # the court's own weekly drift is small
    born = next(m for m in state.inbox.newest_first() if m.subject.startswith("A CHILD IS BORN"))
    assert "Civil morale +3" in born.body
    assert any("A child is born" in e["text"] for e in h.history)
    assert h.stability > stability


def test_births_wait_for_the_cooldown_and_need_a_parent(state):
    h = house(state)
    state.catalog["court"]["births"].update(chance_per_week=1.0, first_turn=1)
    tick(state)
    tick(state)
    assert h.births == 1  # at most one every 26 weeks
    h.last_birth_turn = -99
    for c in h.blood():
        c.age = 70  # nobody of child-bearing age
    tick(state)
    assert h.births == 1


def test_the_birth_roll_never_disturbs_the_campaign_rng(state):
    state.catalog["court"]["births"].update(first_turn=1)
    before = state.rng.getstate()
    court._weekly_births(state, TickReport(state.clock.turn, ""))
    assert state.rng.getstate() == before


def test_an_event_card_can_announce_a_birth(state):
    h = house(state)
    effects = {"court": {"birth": "char_02"}}
    assert any("A CHILD IS BORN" in line for line in preview_effects(state, effects))
    morale = state.player.morale
    changes = apply_effects(state, effects)
    assert h.births == 1 and state.player.morale == pytest.approx(morale + 3)
    assert any("JULIAN" in line for line in changes)


def test_writer_packs_can_use_royal_birth():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import generate_massive_deck as gen

    ctx = {"tax": {}, "techs": {}, "diseases": {}, "disasters": {}, "regions": {}, "cities": [], "city_region": {},
           "characters": {"char_02"}, "parent_category": {}}
    out = gen.translate_card({"id": "x", "title": "X", "category": "DOMESTIC", "description": "A birth.",
                              "choices": [{"label": "a", "effects": {"royal_birth": "char_02"}},
                                          {"label": "b", "effects": {"royal_birth": True}}]}, ctx)
    assert out["choices"][0]["effects"]["court"] == {"birth": "char_02"}
    assert out["choices"][1]["effects"]["court"] == {"birth": True}


# --- persistence and the screen -------------------------------------------------------------------------


def test_the_regency_and_births_survive_save_and_load(state, tmp_path):
    h = crown_a_child(state)
    court.royal_birth(state, "char_02")
    path = save_game(state, tmp_path / "save.json")
    loaded = load_game(path)
    assert loaded.player.dynasty == h
    assert loaded.player.dynasty.regent_id == "char_03" and loaded.player.dynasty.births == 1
    assert "Regency: waste and embezzlement (+10%)" in compute_ledger(loaded).expenses


def test_royal_court_screen_shows_the_regent():
    from src.ui.app import CommandTerminalApp
    from src.ui.views.court import CourtView

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=1984)
        async with app.run_test(size=(200, 60)) as pilot:
            crown_a_child(app.game)
            app.state_changed()
            await pilot.press("0")
            await pilot.pause()
            view = app.screen.query_one(CourtView)
            summary = str(view.query_one("#court-summary").render())
            assert "REGENT DUKE VARGUS" in summary and "COSTS +10%" in summary

    asyncio.run(run())
