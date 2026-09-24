from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Relations are always relative to the reigning Ruler (relabelled on succession).
RULER = "Ruler"
SPOUSE = "Spouse"
HEIR = "Heir"
SIBLING = "Sibling"
COUSIN = "Cousin"
UNCLE_AUNT = "Uncle/Aunt"
DOWAGER = "Dowager"
NOBLE = "Noble"
BLOOD_RELATIONS = (HEIR, SIBLING, COUSIN, UNCLE_AUNT)  # succession order

OFFICES = ("finance", "war", "intelligence")


@dataclass
class Character:
    """A member of the Royal Court: a relative of the Ruler or a powerful noble.

    Stats run 0-20 (8 is an ordinary courtier). Loyalty runs 0-100: below the treason threshold the character
    plots. Influence (0-100) is standing among the officer corps and the provinces, the stuff coups are made of.
    """

    id: str
    name: str
    age: int
    relation: str  # Ruler | Spouse | Heir | Sibling | Cousin | Uncle/Aunt | Dowager | Noble
    role: str  # title or post, e.g. "Duke of Ironvale", "Heir Apparent"
    administration: int
    military: int
    intrigue: int
    traits: list[str] = field(default_factory=list)  # ambitious | loyal | corrupt | genius
    loyalty: float = 50.0
    influence: float = 30.0
    dynasty: bool = True  # of the blood (False for nobles and spouses)
    alive: bool = True
    office: str | None = None  # finance | war | intelligence
    unit_id: str | None = None  # the formation this character commands in the field
    married_to: str | None = None  # off-map power id this character was married into
    imprisoned: bool = False
    ill_weeks: int = 0  # > 0 while sick (the plague in the capital)
    died: str | None = None  # "WK 023: killed in action at ..." once dead

    def has(self, trait: str) -> bool:
        return trait in self.traits

    def stat(self, name: str) -> int:
        return int(getattr(self, name))

    @property
    def at_court(self) -> bool:
        """Present in Aldmark: alive, free, not abroad and not at the front."""
        return self.alive and not self.imprisoned and self.married_to is None and self.unit_id is None


@dataclass
class Dynasty:
    """The ruling house of a nation and its court."""

    name: str
    characters: dict[str, Character] = field(default_factory=dict)
    ruler_id: str = ""
    stability: float = 60.0  # 0-100: the house's grip on the state
    next_audience_turn: int = 0  # when the next petitioner demands an audience
    last_court_turn: int = -99  # when the Lord Protector last held court on his own initiative
    history: list[dict[str, Any]] = field(default_factory=list)  # {turn, text}: births, deaths, successions, plots

    @property
    def ruler(self) -> Character:
        return self.characters[self.ruler_id]

    def living(self) -> list[Character]:
        return [c for c in self.characters.values() if c.alive]

    def blood(self) -> list[Character]:
        """Living members of the house (the ruler included)."""
        return [c for c in self.living() if c.dynasty]

    def minister(self, office: str) -> Character | None:
        return next((c for c in self.living() if c.office == office), None)
