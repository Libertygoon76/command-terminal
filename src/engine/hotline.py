"""The Vosk Hotline: the enemy speaks (Expansion 1.1). Cable texts: data/hotline.json.

Chancellor V. Krov of the Vosk Hegemony does not stay silent. After the week's fighting (HotlineSystem), at
most once every `cooldown` weeks (config `hotline`), a TOP SECRET cable arrives on the direct line:

  vosk_collapsing        Vosk military morale has broken (below armistice_below): the Chancellor asks for an
                         ARMISTICE. Accept and the war ends in a negotiated VICTORY; refuse and fight on.
  kestria_winning        the Vosk have just lost a major battle (big_battle_losses men): a CEASEFIRE proposal.
                         Accept: the Vosk hold their fire for ceasefire_weeks weeks (their AI stays on the
                         defensive), tension falls, the public rejoices, the generals grumble. Refuse: tension rises.
  vosk_winning           the Vosk have just won a major battle, or their troops stand on Kestrian soil: TERMS OF
                         SURRENDER. Reject them, or buy a pause with an indemnity.
  vosk_preparing_assault the Vosk General Staff has just gone over to the ASSAULT: an UNCONDITIONAL WARNING with
                         a demand — pay an indemnity, withdraw our divisions from the Frontier, or defy them.
Replies are ordinary inbox replies (a/b/c) with a deadline; silence is an answer too.

A ceasefire holds while nobody starts a new battle. If fighting breaks out during it, the ceasefire is BROKEN:
tension jumps and every foreign power thinks less of Kestria.
"""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.systems import SimulationSystem, TickReport
from src.models import Email, EmailOption, GameState

EXPIRED = "__expired__"


def _cfg(state: GameState) -> dict:
    return state.config.get("hotline", {})


def cable(state: GameState, trigger: str) -> dict:
    data = state.catalog.get("hotline", {})
    return next(c for c in data.get("cables", []) if c["trigger"] == trigger)


def _email(state: GameState, trigger: str, options: list[EmailOption], on_expire: EmailOption | None = None,
           **terms) -> Email:
    entry = cable(state, trigger)
    body = entry["body"]
    if entry.get("terms"):
        body += "\n\n" + entry["terms"].format(**terms)
    sender = state.catalog.get("hotline", {}).get("sender", "Vosk Hegemony — DIRECT LINE (HOTLINE)")
    return Email(id=f"hotline_{trigger}", sender=sender, subject=f"HOTLINE — {entry['subject']}",
                 classification="TOP SECRET", body=body, options=options,
                 deadline_weeks=int(_cfg(state).get("reply_weeks", 2)) if options else None, on_expire=on_expire)


def ceasefire_offer(state: GameState) -> Email:
    weeks = int(_cfg(state).get("ceasefire_weeks", 4))
    options = [
        EmailOption(id="accept", label=f"Accept a {weeks}-week ceasefire",
                    effects={"ceasefire": weeks, "ai_tension": -20, "morale": 4, "military_morale": -3}),
        EmailOption(id="refuse", label="Refuse: the offensive continues",
                    effects={"ai_tension": 8, "military_morale": 2}),
    ]
    return _email(state, "kestria_winning", options, EmailOption(id=EXPIRED, label="No answer sent.",
                                                                  effects={"ai_tension": 5}), weeks=weeks)


def surrender_terms(state: GameState) -> Email:
    cfg = _cfg(state)
    weeks, indemnity = int(cfg.get("ceasefire_weeks", 4)), int(cfg.get("indemnity", 120000))
    options = [
        EmailOption(id="reject", label="Reject their terms with contempt",
                    effects={"morale": 3, "military_morale": 2, "ai_tension": 5}),
        EmailOption(id="indemnity", label=f"Buy a {weeks}-week pause ({indemnity:,} CR)",
                    effects={"treasury": -indemnity, "ceasefire": weeks, "ai_tension": -15, "morale": -4}),
    ]
    return _email(state, "vosk_winning", options, EmailOption(id=EXPIRED, label="No answer sent.",
                                                               effects={"ai_tension": 5, "morale": -1}),
                  weeks=weeks, indemnity=f"{indemnity:,}")


def ultimatum(state: GameState) -> Email:
    indemnity = int(_cfg(state).get("indemnity", 120000))
    options = [
        EmailOption(id="indemnity", label=f"Pay the indemnity ({indemnity:,} CR)",
                    effects={"treasury": -indemnity, "ai_tension": -25, "morale": -3}),
        EmailOption(id="withdraw", label="Withdraw our divisions from the Frontier trenches",
                    effects={"withdraw_frontier": True, "ai_tension": -35, "military_morale": -8, "morale": -3}),
        EmailOption(id="defy", label="Defy them",
                    effects={"ai_tension": 10, "morale": 3, "military_morale": 2}),
    ]
    return _email(state, "vosk_preparing_assault", options, EmailOption(id=EXPIRED, label="No answer sent.",
                                                                         effects={"ai_tension": 8}),
                  indemnity=f"{indemnity:,}")


def armistice_offer(state: GameState) -> Email:
    options = [
        EmailOption(id="accept", label="Accept the armistice: end the war", effects={"armistice": True, "morale": 10}),
        EmailOption(id="refuse", label="Refuse: nothing but unconditional surrender",
                    effects={"ai_tension": 15, "military_morale": 4, "morale": -2}),
    ]
    return _email(state, "vosk_collapsing", options)


def broken_email(state: GameState) -> Email:
    return _email(state, "ceasefire_broken", [])


def withdraw_from_frontier(state: GameState) -> list:
    """Concession: every Kestrian land formation in the Frontier falls back into the Eastmarch."""
    from src.engine.movement import OrderError, issue_move_order

    world = state.world_map
    fall_back_x = int(_cfg(state).get("withdraw_to_x", 112))
    moved = []
    for unit in list(state.player.units):
        if state.domain(unit) != "land" or not world.in_region("frontier", *unit.location):
            continue
        try:
            issue_move_order(state, unit.id, (fall_back_x, unit.y))
            unit.pending_orders = []  # a political order from the capital: not open to debate
            moved.append(unit)
        except OrderError:
            continue
    return moved


def vosk_on_kestrian_soil(state: GameState, enemy_id: str) -> bool:
    world = state.world_map
    return any(state.domain(u) == "land" and world.owner_at(*u.location) == state.player.id
               for u in state.nations[enemy_id].units)


class HotlineSystem(SimulationSystem):
    """After the fighting: ceasefires tick and may break; the Chancellor may call."""

    name = "hotline"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        if not state.ai_states or state.game_over or "hotline" not in state.catalog:
            return
        cfg = _cfg(state)
        enemy_id = next(iter(state.ai_states))
        enemy = state.nations[enemy_id]
        ai = state.ai_states[enemy_id]
        turn = state.clock.turn
        if state.ceasefire_weeks > 0:
            if any(b.started_turn == turn for b in state.battles.values()):
                state.ceasefire_weeks = 0
                ai.adjust_tension(float(cfg.get("broken_tension", 20)))
                from src.engine.diplomacy import adjust_relation, nations

                for nid in nations(state):
                    adjust_relation(state, nid, state.player.id, float(cfg.get("broken_relations", -10)))
                report.new_messages.append(deliver(state, broken_email(state)))
                report.log.append("THE CEASEFIRE IS BROKEN.")
            else:
                state.ceasefire_weeks -= 1
                if state.ceasefire_weeks == 0:
                    report.log.append("The ceasefire has expired.")
        if state.hotline_turn and turn - state.hotline_turn < int(cfg.get("cooldown", 6)):
            return
        big = int(cfg.get("big_battle_losses", 2500))
        ended = [b for b in state.battles.values() if b.ended_turn == turn]
        email = None
        if enemy.military_morale < float(cfg.get("armistice_below", 20)) and not state.flags.get("armistice_offered"):
            state.flags["armistice_offered"] = turn
            email = armistice_offer(state)
        elif state.ceasefire_weeks == 0 and any(b.victor == state.player.id and b.casualties.get(enemy_id, 0) >= big
                                                for b in ended):
            email = ceasefire_offer(state)
        elif state.ceasefire_weeks == 0 and (
                any(b.victor == enemy_id and b.casualties.get(state.player.id, 0) >= big for b in ended)
                or (vosk_on_kestrian_soil(state, enemy_id) and not state.flags.get("terms_offered"))):
            state.flags["terms_offered"] = turn
            email = surrender_terms(state)
        elif ai.posture == "ASSAULT" and ai.weeks_in_posture == 0 and state.flags.get("hotline_posture") != turn:
            state.flags["hotline_posture"] = turn
            email = ultimatum(state)
        if email is not None:
            state.hotline_turn = turn
            report.new_messages.append(deliver(state, email))
            report.log.append(f"{email.subject.replace('HOTLINE — COMMUNIQUE: ', 'HOTLINE: ')}.")
