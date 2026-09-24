"""The Event Deck: CLASSIFIED DILEMMAS (data/events_deck.json).

Last in each tick, the DilemmaSystem may draw a card: from `first_turn` on, if no card has been drawn in
the last `min_weeks_between` weeks, with `draw_chance`. Eligible cards (conditions: season, minimum
turn, a research project running, a battle being fought, story flags) are weighted by `weight`; a card
is not drawn twice unless it is `repeatable`.

A drawn card becomes `state.pending_dilemma`. The terminal shows it as a modal pop-up and the TickEngine
refuses to advance the week until the Lord Protector picks one of its 2–3 choices. The choice's effects
are applied through the ordinary effect interpreter (effects.py) and a record of the decision is filed
in the inbox.

Debug: `main.py --event <card_id>` forces that card at the next week.
"""

from __future__ import annotations

from src.engine.effects import apply_effects, preview_effects
from src.engine.event_manager import GameOverError, deliver
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import Email, GameState


class DilemmaError(ValueError):
    """An invalid dilemma decision."""


def deck(state: GameState) -> dict:
    return state.catalog.get("events_deck", {"cards": []})


def cards(state: GameState) -> dict[str, dict]:
    return {c["id"]: c for c in deck(state).get("cards", [])}


def card(state: GameState, card_id: str) -> dict:
    if card_id in state.crisis_cards:  # CRITICAL EMERGENCIES built at runtime by the crisis engine
        return state.crisis_cards[card_id]
    table = cards(state)
    if card_id not in table:
        raise DilemmaError(f"Unknown event card {card_id!r}")
    return table[card_id]


def eligible(state: GameState, entry: dict) -> bool:
    from src.engine.weather_engine import season_of

    if entry["id"] in state.used_cards and not entry.get("repeatable"):
        return False
    cond = entry.get("conditions", {})
    if "seasons" in cond and season_of(state) not in cond["seasons"]:
        return False
    if state.clock.turn < int(cond.get("min_turn", 0)):
        return False
    if cond.get("requires_research") and not state.player.research_project:
        return False
    if cond.get("requires_battle") and not any(b.active for b in state.battles.values()):
        return False
    if cond.get("requires_flag") and not state.flags.get(cond["requires_flag"]):
        return False
    if cond.get("forbids_flag") and state.flags.get(cond["forbids_flag"]):
        return False
    for nid, low in cond.get("requires_relation", {}).items():  # off-map nations (Expansion 1.1)
        if nid not in state.foreign or state.foreign[nid]["alignment"] < float(low):
            return False
    if "requires_trade" in cond and state.player.id not in state.foreign.get(cond["requires_trade"], {}).get("trade", []):
        return False
    missing = cond.get("requires_city_without")
    if missing and (missing["city"] not in state.cities or state.cities[missing["city"]].count(missing["building"])):
        return False
    return True


def draw(state: GameState, *, force: bool = False) -> str | None:
    """Maybe draw a card this week. Returns its id (also set as state.pending_dilemma)."""
    if state.game_over or state.pending_dilemma:
        return None
    cfg = deck(state)
    if state.forced_card:
        chosen, state.forced_card = state.forced_card, None
        card(state, chosen)  # validate
    else:
        if state.clock.turn < int(cfg.get("first_turn", 3)):
            return None
        if state.last_card_turn and state.clock.turn - state.last_card_turn < int(cfg.get("min_weeks_between", 3)):
            return None
        if not force and state.rng.random() >= float(cfg.get("draw_chance", 0.15)):
            return None
        pool = [c for c in cfg.get("cards", []) if eligible(state, c)]
        if not pool:
            return None
        chosen = state.rng.choices([c["id"] for c in pool], weights=[float(c.get("weight", 1)) for c in pool], k=1)[0]
    state.pending_dilemma = chosen
    state.used_cards.add(chosen)
    state.last_card_turn = state.clock.turn
    return chosen


def card_text(state: GameState, entry: dict) -> str:
    return fill(entry.get("text", ""), state.text_vars())


def choice_preview(state: GameState, choice: dict) -> list[str]:
    return preview_effects(state, choice.get("effects", {}))


def resolve(state: GameState, choice_id: str) -> list[str]:
    """Apply the chosen option of the pending dilemma. Returns the visible consequences."""
    if state.game_over:
        raise GameOverError("The government has fallen. The terminal is locked.")
    if not state.pending_dilemma:
        raise DilemmaError("No dilemma is awaiting a decision.")
    entry = card(state, state.pending_dilemma)
    choice = next((c for c in entry["choices"] if c["id"] == choice_id), None)
    if choice is None:
        raise DilemmaError(f"The dilemma has no option {choice_id!r}.")
    changes = apply_effects(state, choice.get("effects", {}))
    state.crisis_cards.pop(entry["id"], None)
    state.pending_dilemma = state.dilemma_queue.pop(0) if state.dilemma_queue else None
    body = [
        card_text(state, entry),
        "",
        f"DECISION: {choice['label'].upper()}",
        choice.get("hint", ""),
        "",
        "CONSEQUENCES",
        *([f"  • {line}" for line in changes] or ["  • None immediately visible."]),
        "",
        "— Private Office of the Lord Protector",
    ]
    tpl = state.catalog["generated"]["dilemma"]
    variables = state.text_vars() | {"title": entry["title"]}
    email = Email(id=f"dilemma_{entry['id']}", sender=fill(tpl["sender"], variables),
                  subject=fill(tpl["subject"], variables), classification=tpl.get("classification", "TOP SECRET"),
                  body="\n".join(body))
    delivered = deliver(state, email)
    delivered.read = True
    return changes


class DilemmaSystem(SimulationSystem):
    """Last in the tick: maybe draw a CLASSIFIED DILEMMA for the Lord Protector."""

    name = "dilemmas"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        chosen = draw(state)
        if chosen:
            report.dilemma = chosen
            report.log.append(f"CLASSIFIED DILEMMA: {card(state, chosen)['title']}.")
