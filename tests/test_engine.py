"""Engine tests: no Textual involved."""

import random

import pytest

from src.engine.data_loader import new_game
from src.engine.event_manager import ReplyError, deliver, respond
from src.engine.fail_states import check_fail_state
from src.engine.intel import estimate
from src.engine.tick_engine import build_default_engine
from src.models import Email, EmailOption, FollowUp


@pytest.fixture
def game():
    state = new_game(seed=1984)
    return state, build_default_engine(state)


def first(state, template_id):
    return next(m for m in state.inbox.messages if m.template_id == template_id)


def test_new_game_delivers_turn_one_emails(game):
    state, _ = game
    assert {m.template_id for m in state.inbox.messages} == {"brief_001", "treasury_001", "intel_001"}
    assert all(s.turn > 1 for s in state.schedule)


def test_reply_applies_effects_and_archives(game):
    state, _ = game
    intel = first(state, "intel_001")
    before = state.player.treasury
    changes = respond(state, intel.id, "fund")
    assert state.player.treasury == before - 40000
    assert changes == ["TREASURY −40,000 CR"]
    assert intel.replied and not intel.awaiting_response
    with pytest.raises(ReplyError):
        respond(state, intel.id, "watch")


def test_follow_up_arrives_after_delay(game):
    state, engine = game
    respond(state, first(state, "intel_001").id, "fund")
    follow_ids = {"intel_002_success", "intel_002_burned"}
    for _ in range(2):
        engine.advance()
        assert not follow_ids & {m.template_id for m in state.inbox.messages}
    engine.advance()  # turn 4 = 1 + 3 weeks
    assert follow_ids & {m.template_id for m in state.inbox.messages}


def test_weekly_report_and_income(game):
    state, engine = game
    before = state.player.treasury
    report = engine.advance()
    ledger = state.last_ledger
    assert ledger.net == ledger.total_income - ledger.total_expenses
    assert any(m.template_id == "status_report" for m in report.new_messages)
    # Treasury moved by the ledger plus any on_arrival effects of new mail (none in week 2).
    assert state.player.treasury == before + ledger.net


def test_unanswered_deadline_expires_with_default(game):
    state, engine = game
    intel = first(state, "intel_001")  # deadline 3 weeks
    mil_before = state.player.military_morale
    engine.advance()
    engine.advance()
    assert intel.awaiting_response
    engine.advance()  # turn 4
    assert intel.expired and not intel.awaiting_response
    assert state.player.military_morale < mil_before


def test_revolution_ends_game_with_pinned_alert(game):
    state, engine = game
    state.player.morale = 0
    report = engine.advance()
    assert state.game_over and state.game_over.cause == "revolution"
    alert = state.inbox.newest_first()[0]
    assert alert.pinned and "SYSTEM PURGE" in alert.subject
    with pytest.raises(RuntimeError):
        engine.advance()
    assert report.game_over is state.game_over


def test_bankruptcy_grace_period(game):
    state, engine = game
    state.player.treasury = -10_000
    state.player.military_morale = 100  # isolate the insolvency rule from pay-arrears coups
    grace = state.config["fail_states"]["bankruptcy_grace_weeks"]
    for _ in range(grace - 1):
        engine.advance()
        state.player.treasury = min(state.player.treasury, -10_000)
        assert check_fail_state(state) is None
    engine.advance()
    assert state.game_over and state.game_over.cause == "collapse"


def test_on_arrival_effects_and_weighted_outcome():
    state = new_game(seed=7)
    template = Email(id="t", sender="s", subject="x", classification="SECRET", body="b",
                     on_arrival={"morale": -5})
    morale = state.player.morale
    email = deliver(state, template)
    assert state.player.morale == morale - 5
    assert email.arrival_consequences == ["CIVIL MORALE −5"]
    assert EmailOption.from_dict({"label": "x", "follow_ups": [{"email": "a", "delay_weeks": 0}]}).follow_ups == (
        FollowUp(delay_weeks=1, outcomes=(("a", 1.0),)),
    )


def test_intel_estimates_are_ranges_and_sometimes_lie():
    rng = random.Random(3)
    readings = [estimate(rng, 50_000, 0.5, 0.2) for _ in range(500)]
    assert all(r.low <= r.high for r in readings)
    lies = [r for r in readings if r.misinformation]
    assert 50 < len(lies) < 150
    honest_hits = sum(r.low <= 50_000 <= r.high for r in readings if not r.misinformation)
    assert honest_hits > 0.5 * (500 - len(lies))


@pytest.mark.parametrize("seed", range(40))
def test_random_playthroughs_never_crash(seed):
    state = new_game(seed=seed)
    engine = build_default_engine(state)
    rng = random.Random(seed)
    for _ in range(30):
        for email in state.inbox.awaiting_response():
            if rng.random() < 0.7:
                respond(state, email.id, rng.choice(email.options).id)
        engine.advance()
        if state.game_over:
            break
    ids = [m.id for m in state.inbox.messages]
    assert len(ids) == len(set(ids))
