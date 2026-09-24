"""End-of-turn checks: the three ways to lose (revolution, military coup, state collapse) and the way
to win (VICTORY: ENEMY CAPITULATION).

Victory (config `victory`):
  * OCCUPATION — a Kestrian land formation (not routing) within occupation_radius rows of the enemy capital,
    with no enemy land formation as close, for occupation_weeks consecutive weeks; or
  * COLLAPSE — the enemy treasury is at or below 0 AND its military morale has fallen to
    collapse_military_morale.
Either ends the campaign in triumph: the game locks with a VICTORY modal (restart or exit).
"""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.movement import scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import Email, GameOver, GameState

VICTORY = "victory"


def check_fail_state(state: GameState) -> str | None:
    """Return the cause of the government's fall, or None. Checked in order of severity."""
    cfg = state.config.get("fail_states", {})
    nation = state.player
    if nation.morale <= float(cfg.get("revolution_morale", 0)):
        return "revolution"
    if nation.military_morale <= float(cfg.get("coup_military_morale", 10)):
        return "coup"
    if state.weeks_insolvent >= int(cfg.get("bankruptcy_grace_weeks", 8)):
        return "collapse"
    if nation.treasury <= int(cfg.get("collapse_debt_limit", -1_500_000)):
        return "collapse"
    return None


def enemy_capital(state: GameState) -> tuple[str, tuple[int, int]] | None:
    enemy = state.config.get("victory", {}).get("enemy", "vosk")
    world = state.world_map
    capital = next((f for f in world.features if f.type == "capital" and world.owner_at(f.x, f.y) == enemy), None)
    return (capital.name, (capital.x, capital.y)) if capital else None


def capital_held(state: GameState) -> bool:
    """Kestrian troops in the enemy capital, and no enemy troops as close."""
    found = enemy_capital(state)
    if found is None:
        return False
    _, cell = found
    radius = float(state.config.get("victory", {}).get("occupation_radius", 1.0))
    land = [u for u in state.all_units() if state.domain(u) == "land" and not u.routing]
    ours = [u for u in land if state.is_friendly(u.nation_id) and scaled_distance(state, u.location, cell) <= radius]
    theirs = [u for u in land if not state.is_friendly(u.nation_id) and scaled_distance(state, u.location, cell) <= radius]
    return bool(ours) and not theirs


def check_victory(state: GameState, report: TickReport | None = None) -> str | None:
    """'occupation' or 'collapse' when the enemy capitulates, else None. Tracks consecutive weeks held."""
    cfg = state.config.get("victory", {})
    enemy = state.nations.get(cfg.get("enemy", "vosk"))
    if enemy is None:
        return None
    if state.flags.get("armistice_accepted"):
        return "armistice"
    if capital_held(state):
        state.occupation_weeks += 1
        if state.occupation_weeks == 1 and report is not None:
            report.new_messages.append(deliver(state, capital_email(state)))
            report.log.append(f"KESTRIAN TROOPS IN {enemy_capital(state)[0].upper()}.")
        if state.occupation_weeks >= int(cfg.get("occupation_weeks", 2)):
            return "occupation"
    else:
        state.occupation_weeks = 0
    if enemy.treasury <= 0 and enemy.military_morale <= float(cfg.get("collapse_military_morale", 0)):
        return "collapse"
    return None


def capital_email(state: GameState) -> Email:
    name, (x, y) = enemy_capital(state)
    tpl = state.catalog["generated"]["capital_entered"]
    weeks = state.config.get("victory", {}).get("occupation_weeks", 2)
    body = "\n".join([
        f"FLASH. Kestrian troops have fought their way into {name} (grid {x:03d}-{y:03d}). The Hegemony's government "
        "has fled the ministries. Street fighting continues.",
        "",
        f"If we hold the city for {weeks} consecutive weeks, the Vosk regime cannot survive. Every enemy formation "
        "within reach will be thrown at our men. Reinforce them. Keep them supplied.",
        "",
        "— Frontier Army Operations Staff",
    ])
    variables = state.text_vars() | {"capital": name.upper()}
    return Email(id="capital_entered", sender=fill(tpl["sender"], variables), subject=fill(tpl["subject"], variables),
                 classification=tpl.get("classification", "TOP SECRET"), body=body)


def victory_email(state: GameState, how: str) -> Email:
    enemy = state.nations[state.config.get("victory", {}).get("enemy", "vosk")]
    capital, _ = enemy_capital(state) or ("their capital", (0, 0))
    if how == "occupation":
        cause = (f"Kestrian forces have held {capital} for {state.occupation_weeks} weeks. The {enemy.leader_title} "
                 "has been taken in the ruins of the Chancellery.")
    elif how == "armistice":
        cause = ("The Chancellor's armistice has been signed on the present lines. The Hegemony's army is spent; its "
                 "delegates in Iren have accepted every Kestrian condition that matters.")
    else:
        cause = (f"The {enemy.name} is bankrupt and its army has lost the will to fight. Unpaid regiments are "
                 "marching home; the garrison of " + capital + " has declared for a provisional government.")
    lines = [
        f"At {state.clock.date_str} the {enemy.name} signed an instrument of UNCONDITIONAL SURRENDER.",
        "",
        cause,
        "",
        "Hostilities cease on all fronts at midnight. Our armies will occupy the Vosk Marches pending a peace "
        "conference in the Iren Free Port. Church bells are ringing across the Commonwealth.",
        "",
        f"CAMPAIGN: {state.clock.turn - 1} weeks of war.",
        f"TREASURY: {state.player.treasury:,} {state.currency}   CIVIL MORALE: {state.player.morale:.0f}   "
        f"MILITARY MORALE: {state.player.military_morale:.0f}",
        f"BATTLES FOUGHT: {len(state.battles)}   KESTRIAN FORMATIONS IN THE FIELD: {len(state.player.units)}",
        "",
        "History will remember the Lord Protector.",
    ]
    tpl = state.catalog["generated"]["victory"]
    variables = state.text_vars()
    return Email(id="victory", sender=fill(tpl["sender"], variables), subject=fill(tpl["subject"], variables),
                 classification=tpl.get("classification", "TOP SECRET"), body="\n".join(lines), pinned=True)


class FailStateSystem(SimulationSystem):
    """Runs last. On failure, locks the game and pushes the pinned SYSTEM PURGE alert; on victory, the
    capitulation dispatch. Either way the campaign ends."""

    name = "fail_states"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        cause = check_fail_state(state)
        if cause is not None:
            state.game_over = GameOver(cause=cause, turn=state.clock.turn, date=state.clock.date_str)
            alert = state.catalog["alerts"][cause]
            template = Email(
                id=f"system_purge_{cause}",
                sender=alert["sender"],
                subject=alert["subject"],
                classification="TOP SECRET",
                body=alert["body"],
                pinned=True,
            )
            report.new_messages.append(deliver(state, template))
            report.game_over = state.game_over
            report.log.append(alert["headline"])
            return
        how = check_victory(state, report)
        if how is not None:
            state.game_over = GameOver(cause=VICTORY, turn=state.clock.turn, date=state.clock.date_str)
            report.new_messages.append(deliver(state, victory_email(state, how)))
            report.game_over = state.game_over
            report.log.append("VICTORY: ENEMY CAPITULATION.")
