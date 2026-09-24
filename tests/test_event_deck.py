"""The expanded CLASSIFIED DILEMMA deck, its event chains and the Head Writer's packs
(tools/generate_massive_deck.py)."""

import sys
from pathlib import Path

import pytest

from src.engine import dilemmas
from src.engine.data_loader import new_game
from src.engine.dilemmas import card, eligible, resolve
from src.engine.savegame import load_game, save_game
from src.engine.systems import TickReport


def _deck(state):
    return dilemmas.deck(state)["cards"]


def test_the_deck_is_large_and_full_of_chains():
    state = new_game(seed=1)
    cards = _deck(state)
    links = [f for c in cards for ch in c["choices"] for f in ch.get("follow_ups", [])]
    assert len(cards) >= 50 and len(links) >= 15
    assert {"DOMESTIC", "MILITARY", "DIPLOMACY", "ESPIONAGE", "CRISIS"} <= {c["category"] for c in cards}
    assert all((1 if c.get("chain_only") else 2) <= len(c["choices"]) <= 3 for c in cards)
    # Every chain-only card is reachable from some choice.
    targets = {f.get("card") for f in links} | {o["card"] for f in links for o in f.get("outcomes", [])}
    assert {c["id"] for c in cards if c.get("chain_only")} <= targets


@pytest.mark.parametrize("seed", [3])
def test_every_choice_of_every_card_resolves_cleanly(seed):
    for entry in _deck(new_game(seed=seed)):
        for option in entry["choices"]:
            state = new_game(seed=seed)
            state.pending_dilemma = entry["id"]
            resolve(state, option["id"])  # raises on a bad effect id
            assert state.pending_dilemma is None or state.pending_dilemma != entry["id"]


def test_chain_only_cards_are_never_drawn():
    state = new_game(seed=2)
    assert not eligible(state, card(state, "gemini_vosk_defector_success"))


@pytest.mark.live
def test_paying_the_defector_brings_his_designs_two_weeks_later():
    state = new_game(seed=4)
    state.pending_dilemma = "gemini_vosk_defector"
    resolve(state, "option_1")
    assert state.card_schedule == [{"card": "gemini_vosk_defector_success", "turn": state.clock.turn + 2}]
    system = dilemmas.DilemmaSystem()
    state.clock.turn += 1
    system.on_tick(state, TickReport(state.clock.turn, ""))
    assert state.pending_dilemma != "gemini_vosk_defector_success"
    state.pending_dilemma = None
    state.clock.turn += 1
    report = TickReport(state.clock.turn, "")
    system.on_tick(state, report)
    assert state.pending_dilemma == report.dilemma == "gemini_vosk_defector_success"
    resolve(state, "option_1")
    assert "winter_warfare" in state.player.known_techs  # Cold-Weather Equipment


@pytest.mark.live
def test_interrogating_the_defector_kills_him_a_week_later():
    state = new_game(seed=4)
    state.pending_dilemma = "gemini_vosk_defector"
    resolve(state, "option_2")
    state.clock.turn += 1
    dilemmas.DilemmaSystem().on_tick(state, TickReport(state.clock.turn, ""))
    assert state.pending_dilemma == "gemini_vosk_defector_dead"


def test_branching_chains_pick_one_outcome():
    state = new_game(seed=5)
    state.pending_dilemma = "vael_peace_conference"
    resolve(state, "attend")
    assert len(state.card_schedule) == 1
    assert state.card_schedule[0]["card"] in {"vael_talks_progress", "vael_talks_collapse"}


def test_oakhaven_embargo_only_threatens_high_taxes():
    from src.engine.economy_engine import set_tax_policy

    state = new_game(seed=6)
    entry = card(state, "oakhaven_tax_embargo")
    assert not eligible(state, entry)
    set_tax_policy(state, state.player.id, "high")
    assert eligible(state, entry)
    state.pending_dilemma = entry["id"]
    resolve(state, "lower")
    assert state.player.tax_policy == "normal"


def test_scheduled_chains_survive_save_and_load(tmp_path):
    state = new_game(seed=7)
    state.pending_dilemma = "oakhaven_war_loan"
    resolve(state, "accept")
    path = tmp_path / "save.json"
    save_game(state, path)
    loaded = load_game(path)
    assert loaded.card_schedule == state.card_schedule == [{"card": "loan_due", "turn": state.clock.turn + 8}]


# --- the Head Writer's packs ----------------------------------------------------------------------------


def test_writer_packs_translate_into_the_engine_schema():
    state = new_game(seed=8)
    revolt = card(state, "gemini_tax_revolt")
    assert revolt["conditions"] == {"tax_policies": ["oppressive"]}
    assert revolt["choices"][0]["effects"] == {"tax_policy": "high", "morale": 10, "treasury": -20000}
    blast = card(state, "gemini_tax_revolt_aftermath")
    assert blast["chain_only"] and blast["choices"][0]["effects"]["disaster"] == {"kind": "mine_collapse",
                                                                                   "region": "aldmark"}
    duke = card(state, "gemini_uncle_embezzlement")
    assert duke["conditions"] == {"requires_character": ["char_03"]}
    assert duke["choices"][0]["effects"]["court"] == {"execute": "char_03"}
    assert card(state, "gemini_julian_dead")["category"] == "MILITARY"  # inherited down the chain
    for retired in ("vosk_engineer", "defector_designs", "defector_died"):
        assert retired not in dilemmas.cards(state)


def test_the_generator_rejects_unknown_writer_keys():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import generate_massive_deck as gen

    ctx = {"tax": {}, "techs": {}, "diseases": {}, "disasters": {}, "regions": {}, "cities": [], "city_region": {},
           "characters": {"char_01"}, "parent_category": {}}
    with pytest.raises(gen.WriterPackError, match="unknown card keys"):
        gen.translate_card({"id": "x", "title": "X", "choices": [], "mood": "grim"}, ctx)
    with pytest.raises(gen.WriterPackError, match="unknown character"):
        gen.translate_card({"id": "x", "title": "X", "category": "DOMESTIC",
                            "choices": [{"label": "a", "effects": {"kill_character": "char_99"}}]}, ctx)
