from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

CLASSIFICATIONS = ("UNCLASSIFIED", "CONFIDENTIAL", "SECRET", "TOP SECRET")

EXPIRED_RESPONSE = "__expired__"


@dataclass(frozen=True)
class FollowUp:
    """A delayed consequence: after `delay_weeks`, one email is picked from `outcomes` by weight.

    JSON forms:
        {"email": "report_x", "delay_weeks": 2}
        {"delay_weeks": 3, "chance": 0.8,
         "outcomes": [{"email": "op_success", "weight": 65}, {"email": "op_failure", "weight": 35}]}
    """

    delay_weeks: int
    outcomes: tuple[tuple[str, float], ...]
    chance: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FollowUp:
        if "email" in data:
            outcomes = ((data["email"], 1.0),)
        else:
            outcomes = tuple((o["email"], float(o.get("weight", 1))) for o in data["outcomes"])
        return cls(
            delay_weeks=max(1, int(data.get("delay_weeks", 1))),
            outcomes=outcomes,
            chance=float(data.get("chance", 1.0)),
        )

    @property
    def email_ids(self) -> list[str]:
        return [email_id for email_id, _ in self.outcomes]


@dataclass(frozen=True)
class EmailOption:
    """A reply the player can send (also used for the automatic `on_expire` outcome)."""

    id: str
    label: str
    effects: dict[str, Any] = field(default_factory=dict)
    follow_ups: tuple[FollowUp, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_id: str | None = None) -> EmailOption:
        return cls(
            id=data.get("id", default_id or ""),
            label=data["label"],
            effects=dict(data.get("effects", {})),
            follow_ups=tuple(FollowUp.from_dict(f) for f in data.get("follow_ups", [])),
        )


@dataclass
class Email:
    """A classified dispatch.

    Templates are loaded from data/events and never mutated; `delivered()` makes the
    runtime copy that lives in the Inbox and carries read/reply state.
    """

    id: str
    sender: str
    subject: str
    classification: str
    body: str
    template_id: str = ""
    arrives_turn: int | None = None  # None = only arrives as a follow-up / generated
    options: list[EmailOption] = field(default_factory=list)
    on_arrival: dict[str, Any] = field(default_factory=dict)  # effects applied on delivery
    deadline_weeks: int | None = None
    on_expire: EmailOption | None = None
    intel: dict[str, dict[str, Any]] = field(default_factory=dict)  # fog-of-war estimates for the body
    pinned: bool = False  # always on top, cannot be archived or hidden

    # --- runtime state (delivered copies only) ---
    seq: int = 0
    received_turn: int | None = None
    received_date: str | None = None
    expires_turn: int | None = None
    read: bool = False
    response: str | None = None  # chosen option id, or EXPIRED_RESPONSE
    response_label: str | None = None
    response_turn: int | None = None
    consequences: list[str] = field(default_factory=list)  # visible results of the reply
    arrival_consequences: list[str] = field(default_factory=list)
    intel_truth: dict[str, Any] = field(default_factory=dict)  # hidden: what the estimate really was

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(f"Email {self.id!r}: unknown classification {self.classification!r}")
        if not self.template_id:
            self.template_id = self.id

    # --- derived ------------------------------------------------------------

    @property
    def awaiting_response(self) -> bool:
        return bool(self.options) and self.response is None

    @property
    def replied(self) -> bool:
        return self.response is not None and self.response != EXPIRED_RESPONSE

    @property
    def expired(self) -> bool:
        return self.response == EXPIRED_RESPONSE

    @property
    def archived(self) -> bool:
        """Nothing left to do with it: read, and either answered or never needed an answer."""
        if self.pinned:
            return False
        return self.read and not self.awaiting_response

    @property
    def reply_by_turn(self) -> int | None:
        """Last turn on which a reply is still accepted."""
        return None if self.expires_turn is None else self.expires_turn - 1

    # --- construction -------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Email:
        arrives = data.get("arrives_turn")
        on_expire = data.get("on_expire")
        return cls(
            id=data["id"],
            sender=data["sender"],
            subject=data["subject"],
            classification=data.get("classification", "UNCLASSIFIED"),
            body=data["body"],
            arrives_turn=None if arrives is None else int(arrives),
            options=[EmailOption.from_dict(o) for o in data.get("options", [])],
            on_arrival=dict(data.get("on_arrival", {})),
            deadline_weeks=data.get("deadline_weeks"),
            on_expire=EmailOption.from_dict(on_expire, default_id=EXPIRED_RESPONSE) if on_expire else None,
            intel=dict(data.get("intel", {})),
            pinned=bool(data.get("pinned", False)),
        )

    def delivered(self, *, seq: int, turn: int, date_str: str, body: str) -> Email:
        """Return a fresh runtime copy of this template."""
        return replace(
            self,
            id=f"{self.template_id}#{seq}",
            body=body,
            options=list(self.options),
            seq=seq,
            received_turn=turn,
            received_date=date_str,
            expires_turn=None if self.deadline_weeks is None else turn + int(self.deadline_weeks),
            read=False,
            response=None,
            response_label=None,
            response_turn=None,
            consequences=[],
            arrival_consequences=[],
            intel_truth={},
        )


@dataclass
class Inbox:
    messages: list[Email] = field(default_factory=list)
    next_seq: int = 1

    def allocate_seq(self) -> int:
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def deliver(self, email: Email) -> None:
        self.messages.append(email)

    def get(self, email_id: str) -> Email | None:
        return next((m for m in self.messages if m.id == email_id), None)

    @property
    def unread_count(self) -> int:
        return sum(1 for m in self.messages if not m.read)

    @property
    def awaiting_response_count(self) -> int:
        return sum(1 for m in self.messages if m.awaiting_response)

    def awaiting_response(self) -> list[Email]:
        return [m for m in self.messages if m.awaiting_response]

    def newest_first(self) -> list[Email]:
        return sorted(self.messages, key=lambda m: (m.pinned, m.seq), reverse=True)
