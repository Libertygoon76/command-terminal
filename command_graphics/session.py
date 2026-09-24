"""UI-facing commands and safe map projections. No pygame or Textual dependency.

Never serialize GameState to the renderer: enemy Units contain secret information.
Only projected contacts below may be drawn or inspected by the graphical client.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.engine.data_loader import new_game
from src.engine.electronic_warfare import is_dark, last_report
from src.engine.intel import unit_report
from src.engine.map_overlay import contact_code, displayed_type
from src.engine.recon import ghost_contacts
from src.engine.savegame import load_game, save_game
from src.engine.tick_engine import build_default_engine


@dataclass(frozen=True)
class ContactView:
    id: str
    location: tuple[int, int]
    label: str
    name: str
    kind: str
    side: str
    strength: str
    status: str
    supply: float | None = None
    morale: float | None = None
    can_order: bool = False


class Session:
    def __init__(self, *, seed=None, load=None, save_path="savegame-graphical.json"):
        self.game = load_game(load) if load else new_game(seed=seed)
        self.engine = build_default_engine(self.game)
        self.save_path = Path(save_path)
        self.revision = 0
        self.last_summary = "Command established. Review your dispatches before advancing the week."

    def contacts(self) -> list[ContactView]:
        game = self.game
        result = []
        for unit in game.player.units:
            if is_dark(game, unit):
                report = last_report(game, unit)
                result.append(ContactView(unit.id, (report["x"], report["y"]), unit.designation,
                    unit.name, "unknown", "lost", "Unknown", "CONTACT LOST"))
            else:
                result.append(ContactView(unit.id, unit.location, unit.designation, unit.name,
                    unit.unit_type, "friendly", f"{unit.strength:,}", unit.status.upper(),
                    unit.supply, unit.morale, True))
        for unit in game.visible_hostiles():
            report = unit_report(game, unit)
            result.append(ContactView(unit.id, unit.location, contact_code(game, unit),
                f"{unit.name} (probable)" if report.identified else "Unidentified formation",
                displayed_type(game, unit), "hostile", report.strength.text, "OBSERVED"))
        for contact in ghost_contacts(game):
            result.append(ContactView("ghost:" + contact.code, (contact.last_x, contact.last_y),
                "CONTACT " + contact.code, "Last known enemy position", "unknown", "ghost",
                "Unknown", "STALE CONTACT"))
        return result

    def execute(self, function, *args, **kwargs):
        if self.game.game_over:
            raise ValueError("The campaign has ended. Load a save or start a new campaign.")
        if self.game.pending_dilemma:
            raise ValueError("Resolve the outstanding emergency first.")
        result = function(self.game, *args, **kwargs)
        self.revision += 1
        return result

    def advance(self):
        report = self.engine.advance()
        self.revision += 1
        self.last_summary = "\n".join(report.log) or "No new reports this week."
        return report

    def decide(self, choice):
        from src.engine.dilemmas import resolve
        changes = resolve(self.game, choice)
        self.revision += 1
        return changes

    def save(self):
        return save_game(self.game, self.save_path)

    def load_saved(self):
        # Load successfully before replacing the active campaign.
        game = load_game(self.save_path)
        self.game = game
        self.engine = build_default_engine(game)
        self.revision += 1
        self.last_summary = "Campaign restored."
