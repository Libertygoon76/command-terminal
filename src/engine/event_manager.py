"""Classified email lifecycle: delivery, replies, event chains, and deadlines."""

from __future__ import annotations

from src.engine.effects import apply_effects
from src.engine.intel import generate_readings
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import EXPIRED_RESPONSE, Email, EmailOption, FollowUp, GameState, ScheduledEmail


class GameOverError(RuntimeError):
    """Raised when an action is attempted after the government has fallen."""


class ReplyError(ValueError):
    """Raised for invalid replies (unknown email/option, or already answered)."""


# --- delivery -----------------------------------------------------------------


def deliver(state: GameState, template: Email) -> Email:
    """Instantiate a template into the inbox: fill text, roll intel, apply arrival effects."""
    variables = state.text_vars()
    readings = generate_readings(state, template.intel)
    variables.update({name: reading.text for name, reading in readings.items()})

    email = template.delivered(
        seq=state.inbox.allocate_seq(),
        turn=state.clock.turn,
        date_str=state.clock.date_str,
        body=fill(template.body, variables),
    )
    email.intel_truth = {
        name: {"true_value": r.true_value, "misinformation": r.misinformation} for name, r in readings.items()
    }
    if template.on_arrival:
        email.arrival_consequences = apply_effects(state, template.on_arrival)
    state.inbox.deliver(email)
    return email


def deliver_due_emails(state: GameState) -> list[Email]:
    """Deliver every scheduled email whose turn has come, in schedule order."""
    due = [s for s in state.schedule if s.turn <= state.clock.turn]
    if not due:
        return []
    state.schedule = [s for s in state.schedule if s.turn > state.clock.turn]
    return [deliver(state, state.email_library[s.email_id]) for s in due]


# --- event chains -------------------------------------------------------------


def _pick_outcome(state: GameState, follow_up: FollowUp) -> str | None:
    if state.rng.random() >= follow_up.chance:
        return None
    ids = [email_id for email_id, _ in follow_up.outcomes]
    weights = [weight for _, weight in follow_up.outcomes]
    return state.rng.choices(ids, weights=weights, k=1)[0]


def schedule_follow_ups(state: GameState, follow_ups: tuple[FollowUp, ...]) -> list[ScheduledEmail]:
    scheduled = []
    for follow_up in follow_ups:
        email_id = _pick_outcome(state, follow_up)
        if email_id is None:
            continue
        entry = ScheduledEmail(email_id=email_id, turn=state.clock.turn + follow_up.delay_weeks)
        state.schedule.append(entry)
        scheduled.append(entry)
    return scheduled


def _resolve(state: GameState, email: Email, option: EmailOption, response_id: str) -> list[str]:
    changes = apply_effects(state, option.effects)
    schedule_follow_ups(state, option.follow_ups)
    email.response = response_id
    email.response_label = option.label
    email.response_turn = state.clock.turn
    email.consequences = changes
    email.read = True
    return changes


# --- player actions -----------------------------------------------------------


def mark_read(state: GameState, email_id: str) -> bool:
    """Mark a dispatch as read. Returns True if that changed anything."""
    email = state.inbox.get(email_id)
    if email is None or email.read:
        return False
    email.read = True
    return True


def respond(state: GameState, email_id: str, option_id: str) -> list[str]:
    """Send a reply. Applies effects immediately and schedules any follow-ups.

    Returns human-readable change lines. Raises ReplyError if the email cannot be answered.
    """
    if state.game_over:
        raise GameOverError("The government has fallen. The terminal is locked.")
    email = state.inbox.get(email_id)
    if email is None:
        raise ReplyError(f"No such dispatch: {email_id}")
    if not email.awaiting_response:
        raise ReplyError(f"Dispatch {email_id} has already been answered.")
    option = next((o for o in email.options if o.id == option_id), None)
    if option is None:
        raise ReplyError(f"Dispatch {email_id} has no reply option {option_id!r}.")
    return _resolve(state, email, option, option.id)


def expire_overdue(state: GameState) -> list[Email]:
    """Resolve unanswered dispatches whose deadline has passed. Silence is a decision."""
    expired = []
    for email in state.inbox.messages:
        if email.awaiting_response and email.expires_turn is not None and state.clock.turn >= email.expires_turn:
            outcome = email.on_expire or EmailOption(id=EXPIRED_RESPONSE, label="No response sent.")
            _resolve(state, email, outcome, EXPIRED_RESPONSE)
            email.read = False  # surface the consequence to the player
            expired.append(email)
    return expired


# --- tick system --------------------------------------------------------------


class EventManager(SimulationSystem):
    name = "events"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        expired = expire_overdue(state)
        for email in expired:
            report.log.append(f"Deadline passed: {email.subject}.")
        delivered = deliver_due_emails(state)
        report.new_messages.extend(delivered)
        if delivered:
            report.log.append(f"{len(delivered)} new dispatch(es) received.")
