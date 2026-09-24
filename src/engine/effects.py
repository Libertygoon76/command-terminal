"""Effect interpreter: turns JSON effect dicts into changes on GameState.

Supported keys (all deltas):
    treasury, manpower, population       int
    morale, military_morale               float (points on the 0-100 scale)
    tax_rate                              float (fraction, 0.02 = +2 percentage points)
    stockpiles                            {resource_id: int}
    flags                                 {flag_name: value}   (set, not added)
    ai_tension                            float, applied to every AI nation's hidden tension.
                                          Never shown to the player: they must infer it.
    equipment                             {equipment_id: int}  national stockpile (depots)
    army_morale                           float, added to every formation's morale
    modifier                              {key, value, weeks}  temporary nation modifier, e.g.
                                          factory_efficiency +0.15 for 4 weeks (weeks omitted = permanent)
    research_weeks                        float, weeks of progress on the current research project
    quarantine                            {outbreak, mode}  contain an epidemic (mode fund | cordon)
    relief_unit                           {unit, weeks}  pull a formation off the line for disaster relief
    relations                             {nation: delta}  Kestria's standing with an off-map nation
    rival_relations                       {nation: delta}  the enemy's standing with an off-map nation
    build                                 {city, building}  a city project paid for by the event (queued free)
    city_morale                           {city: delta}  local morale in a city
    ceasefire                             weeks  (Hotline) the enemy holds its fire
    armistice                             true   (Hotline) the war ends in a negotiated victory
    withdraw_frontier                     true   (Hotline) our divisions pull out of the Frontier trenches
    tech                                  "<tech id>"  the technology is acquired at once (unlocks, upgrades)
    tax_policy                            "<policy id>"  the tax policy changes (low | normal | high | oppressive)
    outbreak                              {disease, city}  an epidemic breaks out (raises its emergency)
    disaster                              {kind, region}  a natural disaster strikes (raises its emergency)
"""

from __future__ import annotations

from typing import Any

from src.models import GameState

# effect key -> (display label, Nation adjust method, display format)
NATION_EFFECTS: dict[str, tuple[str, str, str]] = {
    "treasury": ("TREASURY", "adjust_treasury", "money"),
    "manpower": ("MANPOWER", "adjust_manpower", "int"),
    "population": ("POPULATION", "adjust_population", "int"),
    "morale": ("CIVIL MORALE", "adjust_morale", "points"),
    "military_morale": ("MILITARY MORALE", "adjust_military_morale", "points"),
    "tax_rate": ("TAX RATE", "adjust_tax_rate", "rate"),
}
SPECIAL_EFFECTS = {"stockpiles", "flags", "ai_tension", "equipment", "army_morale", "modifier", "research_weeks",
                   "quarantine", "relief_unit", "relations", "rival_relations", "build", "city_morale", "ceasefire",
                   "armistice", "withdraw_frontier", "tech", "tax_policy", "outbreak", "disaster"}
MODIFIER_LABELS = {"factory_efficiency": ("FACTORY OUTPUT", "pct")}
VALID_EFFECT_KEYS = set(NATION_EFFECTS) | SPECIAL_EFFECTS


def validate_effects(effects: dict[str, Any], where: str, resource_ids: set[str],
                     equipment_ids: set[str] | None = None) -> None:
    """Raise ValueError with a designer-friendly message for malformed effect blocks."""
    for key, value in effects.items():
        if key not in VALID_EFFECT_KEYS:
            raise ValueError(f"{where}: unknown effect {key!r} (valid: {sorted(VALID_EFFECT_KEYS)})")
        if key == "stockpiles":
            for rid in value:
                if rid not in resource_ids:
                    raise ValueError(f"{where}: unknown resource {rid!r} in stockpiles effect")
        elif key == "equipment":
            for item in value:
                if equipment_ids is not None and item not in equipment_ids:
                    raise ValueError(f"{where}: unknown equipment {item!r} in equipment effect")
        elif key == "modifier":
            if not isinstance(value, dict) or "key" not in value or not isinstance(value.get("value"), (int, float)):
                raise ValueError(f"{where}: modifier effect needs {{key, value[, weeks]}}, got {value!r}")
        elif key in ("quarantine", "relief_unit", "relations", "rival_relations", "build", "city_morale"):
            if not isinstance(value, dict):
                raise ValueError(f"{where}: {key} effect needs an object, got {value!r}")
        elif key in ("armistice", "withdraw_frontier"):
            continue
        elif key in ("tech", "tax_policy"):
            if not isinstance(value, str):
                raise ValueError(f"{where}: {key} effect needs an id string, got {value!r}")
        elif key in ("outbreak", "disaster"):
            if not isinstance(value, dict):
                raise ValueError(f"{where}: {key} effect needs an object, got {value!r}")
        elif key != "flags" and not isinstance(value, (int, float)):
            raise ValueError(f"{where}: effect {key!r} must be a number, got {value!r}")


def _sign(delta: float) -> str:
    return "+" if delta >= 0 else "−"


def describe(label: str, delta: float, fmt: str, currency: str, unit: str = "") -> str:
    magnitude = abs(delta)
    if fmt == "money":
        value = f"{magnitude:,.0f} {currency}"
    elif fmt == "points":
        value = f"{magnitude:g}"
    elif fmt == "rate":
        value = f"{magnitude * 100:.1f} pts"
    else:
        value = f"{magnitude:,.0f}{(' ' + unit) if unit else ''}"
    return f"{label} {_sign(delta)}{value}"


def apply_effects(state: GameState, effects: dict[str, Any]) -> list[str]:
    """Apply an effect block to the player nation. Returns human-readable change lines."""
    nation = state.player
    changes: list[str] = []
    for key, value in effects.items():
        if key in NATION_EFFECTS:
            if not value:
                continue
            label, method, fmt = NATION_EFFECTS[key]
            getattr(nation, method)(value)
            changes.append(describe(label, value, fmt, state.currency))
        elif key == "stockpiles":
            resources = {r["id"]: r for r in state.catalog.get("resources", [])}
            for rid, amount in value.items():
                if not amount:
                    continue
                nation.adjust_stockpile(rid, amount)
                res = resources.get(rid, {})
                changes.append(describe(res.get("name", rid).upper(), amount, "int", state.currency, res.get("unit", "")))
        elif key == "flags":
            state.flags.update(value)
        elif key == "ai_tension":
            for ai in state.ai_states.values():
                ai.adjust_tension(value)
        elif key == "equipment":
            items = {e["id"]: e for e in state.catalog.get("equipment", [])}
            for item_id, amount in value.items():
                if not amount:
                    continue
                have = nation.national_stockpile.get(item_id, 0)
                nation.national_stockpile[item_id] = max(0, have + int(amount))
                item = items.get(item_id, {})
                changes.append(describe(item.get("name", item_id).upper(), amount, "int", state.currency, item.get("unit", "")))
        elif key == "army_morale":
            if value:
                for unit in nation.units:
                    unit.morale = max(0.0, min(100.0, unit.morale + float(value)))
                changes.append(describe("ARMY MORALE (ALL FORMATIONS)", value, "points", state.currency))
        elif key == "modifier":
            weeks = value.get("weeks")
            if weeks:
                nation.timed_modifiers.append({"key": value["key"], "value": float(value["value"]),
                                               "weeks_left": int(weeks), "source": value.get("source", "")})
            else:
                nation.modifiers[value["key"]] = nation.modifiers.get(value["key"], 0.0) + float(value["value"])
            changes.append(describe_modifier(value))
        elif key == "research_weeks":
            project = nation.research_project
            if project and value:
                nation.research_progress[project] = nation.research_progress.get(project, 0.0) + float(value)
                changes.append(f"RESEARCH {_sign(value)}{abs(value):g} WEEKS ON THE CURRENT PROJECT")
        elif key == "quarantine":
            from src.engine.crisis_engine import quarantine

            sites = quarantine(state, value["outbreak"], value.get("mode", "fund"))
            what = "QUARANTINE PROTOCOLS" if value.get("mode", "fund") == "fund" else "MILITARY CORDON"
            changes.append(f"{what}: {len(sites)} SITE(S) CONTAINED")
        elif key == "relief_unit":
            from src.engine.crisis_engine import assign_relief

            unit = assign_relief(state, value["unit"], int(value.get("weeks", 3)))
            if unit is not None:
                changes.append(f"{unit.designation} ON RELIEF DUTY FOR {value.get('weeks', 3)} WEEKS (CANNOT MOVE OR FIGHT)")
        elif key in ("relations", "rival_relations"):
            from src.engine.diplomacy import adjust_relation, nations, rival_of

            who = nation.id if key == "relations" else rival_of(state, nation.id)
            for nid, delta in value.items():
                if nid in state.foreign and delta:
                    adjust_relation(state, nid, who, float(delta))
                    if key == "relations":
                        changes.append(describe(f"RELATIONS WITH {nations(state)[nid]['name'].upper()}", delta,
                                                "points", state.currency))
        elif key == "build":
            from src.engine.cities import CityError, buildings, order_building

            try:
                order_building(state, value["city"], value["building"], free=True)
                changes.append(f"{buildings(state)[value['building']]['name'].upper()} ORDERED IN {value['city'].upper()} "
                               "(PAID BY THE STATE)")
            except CityError as error:
                changes.append(str(error).upper())
        elif key == "city_morale":
            for city_name, delta in value.items():
                city = state.cities.get(city_name)
                if city is not None and delta:
                    city.local_morale = max(0.0, min(100.0, city.local_morale + float(delta)))
                    changes.append(describe(f"{city_name.upper()} LOCAL MORALE", delta, "points", state.currency))
        elif key == "ceasefire" and value:
            state.ceasefire_weeks = max(state.ceasefire_weeks, int(value))
            changes.append(f"CEASEFIRE IN FORCE FOR {int(value)} WEEKS")
        elif key == "armistice" and value:
            state.flags["armistice_accepted"] = True
            changes.append("ARMISTICE ACCEPTED: THE WAR WILL END")
        elif key == "withdraw_frontier" and value:
            from src.engine.hotline import withdraw_from_frontier

            moved = withdraw_from_frontier(state)
            changes.append(f"{len(moved)} FORMATION(S) ORDERED OUT OF THE FRONTIER")
        elif key == "tech":
            from src.engine.research import complete, techs

            if value not in nation.known_techs:
                complete(state, nation, value)
                changes.append(f"TECHNOLOGY ACQUIRED: {techs(state)[value]['name'].upper()}")
        elif key == "tax_policy":
            from src.engine.economy_engine import set_tax_policy

            policy = set_tax_policy(state, nation.id, value)
            changes.append(f"TAX POLICY: {policy['name'].upper()} ({policy['rate']:.0%})")
        elif key == "outbreak":
            from src.engine.crisis_engine import disease, start_outbreak

            if start_outbreak(state, value["disease"], f"city:{value['city']}"):
                changes.append(f"{disease(state, value['disease'])['name'].upper()} BREAKS OUT IN {value['city'].upper()}")
        elif key == "disaster":
            from src.engine.crisis_engine import start_disaster

            if start_disaster(state, value["kind"], value.get("region")):
                changes.append(f"{value['kind'].replace('_', ' ').upper()} STRIKES")
    return changes


def describe_modifier(value: dict[str, Any]) -> str:
    label, fmt = MODIFIER_LABELS.get(value["key"], (value["key"].replace("_", " ").upper(), "pct"))
    amount = float(value["value"])
    text = f"{label} {_sign(amount)}{abs(amount) * 100:.0f}%" if fmt == "pct" else f"{label} {_sign(amount)}{abs(amount):g}"
    return text + (f" FOR {value['weeks']} WEEKS" if value.get("weeks") else "")


def preview_effects(state: GameState, effects: dict[str, Any]) -> list[str]:
    """What a choice will visibly do, without applying it (hidden effects such as ai_tension are left out)."""
    lines: list[str] = []
    for key, value in effects.items():
        if key in NATION_EFFECTS and value:
            label, _, fmt = NATION_EFFECTS[key]
            lines.append(describe(label, value, fmt, state.currency))
        elif key == "stockpiles":
            resources = {r["id"]: r for r in state.catalog.get("resources", [])}
            lines += [describe(resources.get(r, {}).get("name", r).upper(), v, "int", state.currency,
                               resources.get(r, {}).get("unit", "")) for r, v in value.items() if v]
        elif key == "equipment":
            items = {e["id"]: e for e in state.catalog.get("equipment", [])}
            lines += [describe(items.get(i, {}).get("name", i).upper(), v, "int", state.currency,
                               items.get(i, {}).get("unit", "")) for i, v in value.items() if v]
        elif key == "army_morale" and value:
            lines.append(describe("ARMY MORALE", value, "points", state.currency))
        elif key == "modifier":
            lines.append(describe_modifier(value))
        elif key == "research_weeks" and value:
            lines.append(f"RESEARCH {_sign(value)}{abs(value):g} WEEKS")
        elif key == "quarantine":
            lines.append("EPIDEMIC CLEARED IN 2 WEEKS, SPREAD STOPPED" if value.get("mode", "fund") == "fund"
                         else "SPREAD STOPPED (NO CURE)")
        elif key == "relief_unit":
            unit = state.unit(value["unit"])
            label = unit.designation if unit else value["unit"]
            lines.append(f"{label} CANNOT MOVE OR FIGHT FOR {value.get('weeks', 3)} WEEKS")
        elif key == "relations":
            from src.engine.diplomacy import nations

            lines += [describe(f"RELATIONS WITH {nations(state)[n]['name'].upper()}", d, "points", state.currency)
                      for n, d in value.items() if n in nations(state) and d]
        elif key == "build":
            from src.engine.cities import buildings

            lines.append(f"NEW {buildings(state)[value['building']]['name'].upper()} IN {value['city'].upper()} (FREE)")
        elif key == "city_morale":
            lines += [describe(f"{c.upper()} LOCAL MORALE", d, "points", state.currency) for c, d in value.items() if d]
        elif key == "tech":
            from src.engine.research import techs

            lines.append(f"TECHNOLOGY: {techs(state).get(value, {}).get('name', value).upper()}")
        elif key == "tax_policy":
            lines.append(f"TAX POLICY → {value.upper()}")
    return lines


def tick_timed_modifiers(nation) -> list[dict[str, Any]]:
    """Age temporary modifiers by one week; returns those that expired."""
    expired = []
    for mod in list(nation.timed_modifiers):
        mod["weeks_left"] -= 1
        if mod["weeks_left"] <= 0:
            nation.timed_modifiers.remove(mod)
            expired.append(mod)
    return expired
