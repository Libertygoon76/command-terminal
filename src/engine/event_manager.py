from __future__ import annotations

from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import Email, GameState


def deliver_due_emails(state: GameState) -> list[Email]:
    """Move every pending email whose arrival turn has come into the inbox."""
    due = [e for e in state.pending_emails if e.arrives_turn <= state.clock.turn]
    if not due:
        return []
    state.pending_emails = [e for e in state.pending_emails if e.arrives_turn > state.clock.turn]

    variables = state.text_vars()
    delivered = []
    for template in due:
        email = template.delivered(state.clock.turn, state.clock.date_str, body=fill(template.body, variables))
        state.inbox.deliver(email)
        delivered.append(email)
    return delivered


class EventManager(SimulationSystem):
    """Delivers scheduled emails. Phase 2: triggers, chains, and reply effects."""

    name = "events"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        delivered = deliver_due_emails(state)
        report.new_messages.extend(delivered)
        if delivered:
            report.log.append(f"{len(delivered)} new dispatch(es) received.")
