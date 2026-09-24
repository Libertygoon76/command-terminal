"""Research & Development.

Each nation researches one technology at a time from data/tech_tree.json. A project costs its
`weekly_cost` in treasury every week (on the ledger) and completes after `weeks` weeks of work.
Progress is kept per tech, so switching projects loses no work. While the treasury is at or below
0 the laboratories go unpaid and nothing progresses (config research.halt_when_bankrupt).

On completion the tech becomes known:
  * its `unlocks` (equipment.json ids) become producible on the Economy screen;
  * unit templates with `upgrades` for it grow their establishment (e.g. Kevlar vests for every man),
    so the logistics pipeline starts delivering the new kit from the national stockpile;
  * `effects` apply as nation modifiers: factory_efficiency (production output), stance_bonus
    (defense bonus for a stance);
  * the player receives an "R&D BREAKTHROUGH" dispatch.
AI nations pick their next project automatically (cheapest available first).
"""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import Email, GameState, Nation


class ResearchError(ValueError):
    """An invalid research decision."""


def techs(state: GameState) -> dict[str, dict]:
    return {t["id"]: t for t in state.catalog["tech_tree"]["techs"]}


def status(state: GameState, nation: Nation, tech_id: str) -> str:
    """KNOWN | ACTIVE | AVAILABLE | LOCKED (prerequisites missing)."""
    tech = techs(state)[tech_id]
    if tech_id in nation.known_techs:
        return "KNOWN"
    if nation.research_project == tech_id:
        return "ACTIVE"
    if all(req in nation.known_techs for req in tech.get("requires", [])):
        return "AVAILABLE"
    return "LOCKED"


def weeks_remaining(state: GameState, nation: Nation, tech_id: str) -> float:
    tech = techs(state)[tech_id]
    return max(0.0, float(tech["weeks"]) - nation.research_progress.get(tech_id, 0.0))


def start_research(state: GameState, nation_id: str, tech_id: str) -> dict:
    if state.game_over:
        raise ResearchError("The government has fallen. The terminal is locked.")
    nation = state.nations[nation_id]
    table = techs(state)
    if tech_id not in table:
        raise ResearchError(f"Unknown technology: {tech_id}")
    current = status(state, nation, tech_id)
    if current == "KNOWN":
        raise ResearchError(f"{table[tech_id]['name']} is already known.")
    if current == "LOCKED":
        missing = [table[r]["name"] for r in table[tech_id].get("requires", []) if r not in nation.known_techs]
        raise ResearchError(f"{table[tech_id]['name']} requires: {', '.join(missing)}.")
    nation.research_project = tech_id
    return table[tech_id]


def stop_research(state: GameState, nation_id: str) -> None:
    state.nations[nation_id].research_project = None


def apply_effects(nation: Nation, tech: dict) -> None:
    for key, value in tech.get("effects", {}).items():
        if key == "stance_bonus":
            for stance, bonus in value.items():
                name = f"stance_defense:{stance}"
                nation.modifiers[name] = nation.modifiers.get(name, 0.0) + float(bonus)
        else:
            nation.modifiers[key] = nation.modifiers.get(key, 0.0) + float(value)


def complete(state: GameState, nation: Nation, tech_id: str) -> dict:
    tech = techs(state)[tech_id]
    nation.known_techs.add(tech_id)
    nation.research_progress[tech_id] = float(tech["weeks"])
    if nation.research_project == tech_id:
        nation.research_project = None
    apply_effects(nation, tech)
    return tech


def describe_effects(effects: dict) -> list[str]:
    lines = []
    for key, value in effects.items():
        if key == "stance_bonus":
            lines += [f"{stance.upper()} stance: +{bonus:.0%} defense" for stance, bonus in value.items()]
        elif key == "factory_efficiency":
            lines.append(f"All factory output: +{value:.0%}")
        else:
            lines.append(f"{key.replace('_', ' ')}: {value}")
    return lines


def breakthrough_email(state: GameState, tech: dict) -> Email:
    items = {e["id"]: e for e in state.catalog["equipment"]}
    unlocked = [items[i]["name"] for i in tech.get("unlocks", []) if i in items]
    upgraded = [t["name"] for t in state.catalog["units"]["units"] if tech["id"] in t.get("upgrades", {})]
    lines = [
        f"PROJECT COMPLETE: {tech['name'].upper()}",
        f"Branch: {tech.get('branch', '?').replace('_', ' ').title()} · {tech['weeks']} weeks of research",
        "",
    ]
    if tech.get("notes"):
        lines += [tech["notes"], ""]
    if unlocked:
        lines += ["NEW PRODUCTION LINES AVAILABLE (Economy screen)", *[f"  + {name}" for name in unlocked], ""]
    if upgraded:
        lines += ["ESTABLISHMENT REVISED — formations will now draw the new equipment from the national stockpile:",
                  *[f"  • {name}" for name in upgraded], ""]
    effects = tech.get("effects", {})
    if effects:
        lines += ["STANDING EFFECTS", *[f"  • {line}" for line in describe_effects(effects)], ""]
    if unlocked:
        lines += ["Nothing reaches the front until factories are assigned to build it.", ""]
    lines += ["— Directorate of Research & Development"]
    tpl = state.catalog["generated"]["breakthrough"]
    variables = state.text_vars() | {"tech": tech["name"]}
    return Email(id="rnd_breakthrough", sender=fill(tpl["sender"], variables), subject=fill(tpl["subject"], variables),
                 classification=tpl.get("classification", "SECRET"), body="\n".join(lines))


def _ai_pick(state: GameState, nation: Nation) -> None:
    options = [t for t in techs(state) if status(state, nation, t) == "AVAILABLE"]
    if options:
        table = techs(state)
        nation.research_project = min(options, key=lambda t: (table[t]["weeks"] * table[t]["weekly_cost"], t))


class ResearchSystem(SimulationSystem):
    name = "research"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        halt = state.config.get("research", {}).get("halt_when_bankrupt", True)
        for nation_id, nation in state.nations.items():
            if nation_id in state.ai_states and nation.research_project is None:
                _ai_pick(state, nation)
            tech_id = nation.research_project
            if tech_id is None:
                continue
            if halt and nation.bankrupt:
                if state.is_friendly(nation_id):
                    report.log.append("Research halted: laboratories unpaid.")
                continue
            from src.engine.court import research_bonus

            nation.research_progress[tech_id] = nation.research_progress.get(tech_id, 0.0) + 1.0 + \
                research_bonus(state, nation)
            if weeks_remaining(state, nation, tech_id) <= 0:
                tech = complete(state, nation, tech_id)
                if state.is_friendly(nation_id):
                    report.new_messages.append(deliver(state, breakthrough_email(state, tech)))
                    report.log.append(f"R&D breakthrough: {tech['name']}.")
