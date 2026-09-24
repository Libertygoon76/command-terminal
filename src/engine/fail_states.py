"""End-of-turn loss checks: revolution, military coup, state collapse."""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.systems import SimulationSystem, TickReport
from src.models import Email, GameOver, GameState


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


class FailStateSystem(SimulationSystem):
    """Runs last. On failure, locks the game and pushes the pinned SYSTEM PURGE alert."""

    name = "fail_states"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        cause = check_fail_state(state)
        if cause is None:
            return
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
