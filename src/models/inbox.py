from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

CLASSIFICATIONS = ("UNCLASSIFIED", "CONFIDENTIAL", "SECRET", "TOP SECRET")


@dataclass(frozen=True)
class EmailOption:
    """A reply the player can send. `effects` are interpreted by the event manager (Phase 2)."""

    id: str
    label: str
    effects: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmailOption:
        return cls(id=data["id"], label=data["label"], effects=dict(data.get("effects", {})))


@dataclass
class Email:
    """A classified dispatch. Templates live in data/events; delivered copies live in the Inbox."""

    id: str
    sender: str
    subject: str
    classification: str
    body: str
    arrives_turn: int = 1
    options: list[EmailOption] = field(default_factory=list)
    # Runtime state, set on delivery / interaction.
    received_turn: int | None = None
    received_date: str | None = None
    read: bool = False
    response: str | None = None  # id of the chosen EmailOption

    def __post_init__(self) -> None:
        if self.classification not in CLASSIFICATIONS:
            raise ValueError(f"Email {self.id!r}: unknown classification {self.classification!r}")

    @property
    def awaiting_response(self) -> bool:
        return bool(self.options) and self.response is None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Email:
        return cls(
            id=data["id"],
            sender=data["sender"],
            subject=data["subject"],
            classification=data.get("classification", "UNCLASSIFIED"),
            body=data["body"],
            arrives_turn=int(data.get("arrives_turn", 1)),
            options=[EmailOption.from_dict(o) for o in data.get("options", [])],
        )

    def delivered(self, turn: int, date_str: str, body: str | None = None) -> Email:
        """Return a fresh delivered copy of this template."""
        return replace(
            self,
            body=self.body if body is None else body,
            options=list(self.options),
            received_turn=turn,
            received_date=date_str,
            read=False,
            response=None,
        )


@dataclass
class Inbox:
    messages: list[Email] = field(default_factory=list)

    def deliver(self, email: Email) -> None:
        self.messages.append(email)

    @property
    def unread_count(self) -> int:
        return sum(1 for m in self.messages if not m.read)

    @property
    def awaiting_response_count(self) -> int:
        return sum(1 for m in self.messages if m.awaiting_response)

    def newest_first(self) -> list[Email]:
        # Stable sort keeps JSON order within the same turn.
        return sorted(self.messages, key=lambda m: m.received_turn or 0, reverse=True)
