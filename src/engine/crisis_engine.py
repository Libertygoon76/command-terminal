"""The home front: epidemics and natural disasters (data/crises.json).

A nation does not stop suffering because it is at war. Each week the CrisisSystem may start an EPIDEMIC
(Trench Typhus in a front-line formation, Industrial Influenza in an industrial centre, Cholera in a city)
or a NATURAL DISASTER (Severe Flooding, Earthquake, Mine Collapse), and runs the ones already under way.

EPIDEMICS (`state.infections`: site -> infection; a site is "city:<name>" or "unit:<id>")
  * an infected SETTLEMENT costs civil morale every week and cuts factory output (industrial centres
    doubly; production.forecast applies `output_penalty`);
  * an infected FORMATION loses men to disease every week (no battle required) and morale;
  * each week an unchecked site may SPREAD the disease to the nearest clean settlement or formation;
    infections burn out only slowly on their own;
  * a new outbreak raises a CRITICAL EMERGENCY (a modal that pauses the game): fund QUARANTINE PROTOCOLS
    (treasury: every site stops spreading and is cleared within two weeks), throw a military CORDON round
    it (stops the spread, cures nothing, costs morale), or leave it to the doctors. While it keeps spreading
    unchecked the emergency comes back. Researching FIELD MEDICINE clears every infection within two weeks
    and halves the chance of new outbreaks.

NATURAL DISASTERS wreck rail and road (like scorched earth: Combat Engineers repair it), kill civilians and
hurt morale, and raise a CRITICAL EMERGENCY with three hard choices:
  1. FUND RELIEF — 50,000 CR;
  2. DEPLOY THE MILITARY — the nearest formation not in battle is pulled out for relief work: it cannot
     move or fight (firepower x0.2) for three weeks; civil morale rises;
  3. IGNORE — a massive drop in civil morale, one step closer to Protocol Zero.

Emergency cards are built at runtime (`state.crisis_cards`) and shown with the same modal as the event
deck; if two come up in one week, the second waits in `state.dilemma_queue`.
"""

from __future__ import annotations

import math

from src.engine.movement import OrderError, scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import GameState, Unit

FIELD_MEDICINE = "field_medicine"


def _cfg(state: GameState) -> dict:
    return state.catalog.get("crises", {})


def disease(state: GameState, disease_id: str) -> dict:
    return _cfg(state)["diseases"][disease_id]


# --- sites -------------------------------------------------------------------------------------


def settlements(state: GameState) -> list:
    world = state.world_map
    return [f for f in world.features if world.owner_at(f.x, f.y) == state.player.id]


def site_location(state: GameState, site: str) -> tuple[int, int] | None:
    kind, name = site.split(":", 1)
    if kind == "city":
        feature = state.world_map.feature_named(name)
        return (feature.x, feature.y) if feature else None
    unit = state.unit(name)
    return unit.location if unit else None


def site_label(state: GameState, site: str) -> str:
    kind, name = site.split(":", 1)
    if kind == "city":
        return name
    unit = state.unit(name)
    return f"{unit.designation} {unit.name}" if unit else name


def infection_of(state: GameState, unit_or_city) -> dict | None:
    key = f"unit:{unit_or_city.id}" if isinstance(unit_or_city, Unit) else f"city:{unit_or_city}"
    return state.infections.get(key)


def outbreak_sites(state: GameState, outbreak: str) -> list[str]:
    return sorted(site for site, inf in state.infections.items() if inf["outbreak"] == outbreak)


def outbreaks(state: GameState) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for site, inf in sorted(state.infections.items()):
        out.setdefault(inf["outbreak"], []).append(site)
    return out


def output_penalty(state: GameState, nation) -> float:
    """Factory output lost to sick workers in infected settlements (0..max_output_penalty)."""
    if nation.id != state.player.id:
        return 0.0
    world = state.world_map
    total = 0.0
    for site, inf in state.infections.items():
        if site.startswith("city:"):
            feature = world.feature_named(site[5:])
            weight = 2.0 if feature and feature.type == "industrial" else 1.0
            total += float(disease(state, inf["disease"]).get("factory_output", 0.0)) * weight
    return min(float(_cfg(state).get("max_output_penalty", 0.5)), total)


# --- outbreaks ----------------------------------------------------------------------------------


def infect(state: GameState, site: str, disease_id: str, outbreak: str) -> None:
    state.infections[site] = {"disease": disease_id, "outbreak": outbreak, "since": state.clock.turn,
                              "cure_in": None, "cordon": False}
    others = [i for i in state.infections.values() if i["outbreak"] == outbreak and i is not state.infections[site]]
    if others and (others[0]["cure_in"] is not None or others[0]["cordon"]):  # joins a contained outbreak
        state.infections[site]["cordon"] = others[0]["cordon"]
        state.infections[site]["cure_in"] = others[0]["cure_in"]


def start_outbreak(state: GameState, disease_id: str | None = None, site: str | None = None) -> str | None:
    """Begin a new outbreak. Returns its id (also raises the emergency card)."""
    cfg = _cfg(state)
    rng = state.rng
    if disease_id is None:
        table = cfg["diseases"]
        ids = sorted(table)
        disease_id = rng.choices(ids, weights=[float(table[i].get("weight", 1)) for i in ids], k=1)[0]
    info = disease(state, disease_id)
    if site is None:
        candidates = _origins(state, info.get("starts_in", "city"))
        candidates = [c for c in candidates if c not in state.infections]
        if not candidates:
            return None
        site = rng.choice(candidates)
    outbreak = f"OB-{state.clock.turn:03d}-{disease_id}"
    infect(state, site, disease_id, outbreak)
    state.flags[f"outbreak_nag:{outbreak}"] = state.clock.turn
    raise_emergency(state, outbreak_card(state, outbreak, new=True))
    return outbreak


def _origins(state: GameState, kind: str) -> list[str]:
    frontline_x = int(_cfg(state).get("frontline_x", 110))
    towns = settlements(state)
    if kind == "frontline":
        units = [u for u in state.player.units if state.domain(u) == "land" and u.x >= frontline_x]
        return [f"unit:{u.id}" for u in units] or [f"city:{f.name}" for f in towns]
    if kind == "industry":
        found = [f"city:{f.name}" for f in towns if f.type == "industrial"]
        return found or [f"city:{f.name}" for f in towns]
    return [f"city:{f.name}" for f in towns if f.type in ("capital", "city", "port", "industrial")] or \
        [f"city:{f.name}" for f in towns]


def spread(state: GameState) -> list[tuple[str, str]]:
    """Unchecked sites may infect their nearest clean neighbour. Returns (source, new site) pairs."""
    new = []
    for site, inf in sorted(state.infections.items()):
        if inf["cordon"] or inf["cure_in"] is not None:
            continue
        info = disease(state, inf["disease"])
        if state.rng.random() >= float(info.get("spread_chance", 0.2)):
            continue
        origin = site_location(state, site)
        if origin is None:
            continue
        reach = float(info.get("spread_range_city" if site.startswith("city:") else "spread_range_unit", 3))
        targets = [(scaled_distance(state, origin, (f.x, f.y)), f"city:{f.name}") for f in settlements(state)]
        targets += [(scaled_distance(state, origin, u.location), f"unit:{u.id}") for u in state.player.units
                    if state.domain(u) == "land"]
        targets = sorted((d, t) for d, t in targets if t not in state.infections and d <= reach)
        if targets:
            target = targets[0][1]
            infect(state, target, inf["disease"], inf["outbreak"])
            new.append((site, target))
    return new


def quarantine(state: GameState, outbreak: str, mode: str = "fund") -> list[str]:
    """Contain an outbreak: 'fund' (cleared in quarantine_weeks) or 'cordon' (stops spreading only)."""
    sites = outbreak_sites(state, outbreak)
    weeks = int(_cfg(state).get("quarantine_weeks", 2))
    for site in sites:
        if mode == "fund":
            current = state.infections[site]["cure_in"]
            state.infections[site]["cure_in"] = weeks if current is None else min(current, weeks)
        state.infections[site]["cordon"] = True
    return sites


def quarantine_cost(state: GameState, outbreak: str) -> int:
    cfg = _cfg(state)
    return int(cfg.get("quarantine_base", 30000)) + int(cfg.get("quarantine_per_site", 8000)) * len(outbreak_sites(state, outbreak))


def run_epidemics(state: GameState, report: TickReport) -> None:
    cfg = _cfg(state)
    nation = state.player
    medicine = FIELD_MEDICINE in nation.known_techs
    # Toll.
    for site, inf in sorted(state.infections.items()):
        info = disease(state, inf["disease"])
        if site.startswith("city:"):
            nation.adjust_morale(-float(info.get("city_morale", 1.0)))
        else:
            unit = state.unit(site[5:])
            if unit is None:
                continue
            lost = min(unit.strength - 1, int(math.ceil(unit.strength * float(info.get("unit_attrition", 0.02)))))
            if lost > 0:
                unit.strength -= lost
                report.log.append(f"{info['name'].upper()}: {unit.designation} lost {lost:,} men to disease.")
            unit.morale = max(0.0, unit.morale - float(info.get("unit_morale", 2)))
    # Cures, burnout, dead hosts.
    for site in sorted(state.infections):
        inf = state.infections[site]
        if site.startswith("unit:") and state.unit(site[5:]) is None:
            del state.infections[site]
            continue
        if medicine:
            weeks = int(cfg.get("medicine_weeks", 2))
            inf["cure_in"] = weeks if inf["cure_in"] is None else min(inf["cure_in"], weeks)
        if inf["cure_in"] is not None:
            inf["cure_in"] -= 1
            if inf["cure_in"] <= 0:
                del state.infections[site]
                report.log.append(f"{site_label(state, site)}: the {disease(state, inf['disease'])['name'].lower()} is over.")
                continue
        elif state.rng.random() < float(disease(state, inf["disease"]).get("burnout_chance", 0.05)):
            del state.infections[site]
    # Spread, and nag while an outbreak runs unchecked.
    fresh = spread(state)
    for source, target in fresh:
        report.log.append(f"EPIDEMIC SPREADS: {site_label(state, target)} infected.")
    for outbreak in sorted({state.infections[t]["outbreak"] for _, t in fresh if t in state.infections}):
        last = int(state.flags.get(f"outbreak_nag:{outbreak}", 0))
        if state.clock.turn - last >= int(cfg.get("nag_weeks", 3)):
            state.flags[f"outbreak_nag:{outbreak}"] = state.clock.turn
            raise_emergency(state, outbreak_card(state, outbreak, new=False))
    # New outbreaks.
    chance = float(cfg.get("outbreak_chance", 0.06)) * (0.5 if medicine else 1.0)
    if state.clock.turn >= int(cfg.get("first_turn", 4)) and state.rng.random() < chance:
        outbreak = start_outbreak(state)
        if outbreak:
            report.log.append(f"OUTBREAK: {site_label(state, outbreak_sites(state, outbreak)[0])}.")


def outbreak_card(state: GameState, outbreak: str, new: bool) -> dict:
    sites = outbreak_sites(state, outbreak)
    inf = state.infections[sites[0]]
    info = disease(state, inf["disease"])
    cfg = _cfg(state)
    cost = quarantine_cost(state, outbreak)
    where = ", ".join(site_label(state, s) for s in sites)
    if new:
        title = f"CRITICAL EMERGENCY: {info['name'].upper()} OUTBREAK"
        text = (f"The Ministry of Health reports an outbreak of {info['name'].lower()} at {where}. "
                f"{info.get('description', '')}\n\nIf it is not contained it will spread: to neighbouring towns, "
                "to the formations passing through them, into the factories. Every infected town costs civil morale "
                "and factory output each week; every infected formation loses men in their beds.")
    else:
        title = f"CRITICAL EMERGENCY: THE {info['name'].upper()} IS SPREADING"
        text = (f"The {info['name'].lower()} has not been contained. Infected: {where} ({len(sites)} site(s)).\n\n"
                "The Chief Medical Officer asks again for funds for quarantine protocols before the epidemic "
                "reaches the capital.")
    cordon = cfg.get("cordon", {})
    choices = [
        {"id": "quarantine", "label": "Fund Quarantine Protocols",
         "hint": f"Isolation hospitals, delousing stations, clean water. Every site is cleared within "
                 f"{cfg.get('quarantine_weeks', 2)} weeks and stops spreading.",
         "effects": {"treasury": -cost, "quarantine": {"outbreak": outbreak, "mode": "fund"}}},
        {"id": "cordon", "label": "Military Cordon",
         "hint": "Troops seal the infected districts. The spread stops; nobody inside is cured.",
         "effects": {"morale": cordon.get("morale", -4), "military_morale": cordon.get("military_morale", -2),
                     "quarantine": {"outbreak": outbreak, "mode": "cordon"}}},
        {"id": "ignore", "label": "Leave It to the Doctors",
         "hint": "No money, no troops. It may burn out. It may not.",
         "effects": {"morale": cfg.get("ignore_outbreak_morale", -2)}},
    ]
    return {"id": f"crisis_{outbreak}_{state.clock.turn}", "title": title, "category": "EPIDEMIC",
            "emergency": True, "text": text, "choices": choices}


# --- natural disasters -----------------------------------------------------------------------------------


def start_disaster(state: GameState, kind: str | None = None, region_id: str | None = None,
                   report: TickReport | None = None) -> str | None:
    """Strike a Kestrian region. Returns the emergency card id."""
    from src.engine.engineering import DESTROYED, invalidate_terrain_caches, set_cell

    cfg = _cfg(state)
    rng = state.rng
    world = state.world_map
    table = cfg["disasters"]
    kestrian = sorted(r.id for r in world.regions.values() if r.owner == state.player.id)
    if kind is None:
        ids = sorted(k for k in table if any(world.regions[r].terrain in table[k]["terrains"] for r in kestrian))
        if not ids:
            return None
        kind = rng.choices(ids, weights=[float(table[k].get("weight", 1)) for k in ids], k=1)[0]
    info = table[kind]
    if region_id is None:
        options = [r for r in kestrian if world.regions[r].terrain in info["terrains"]] or kestrian
        region_id = rng.choice(options)
    region = world.regions[region_id]
    tracks = sorted(c for c, k in world.transport.items() if k in DESTROYED and world.in_region(region_id, *c))
    if not tracks:
        return None
    center = rng.choice(tracks)
    radius = float(info.get("radius", 3))
    lo, hi = info.get("cells", [4, 8])
    near = sorted((scaled_distance(state, c, center), c) for c, k in world.transport.items()
                  if k in DESTROYED and scaled_distance(state, c, center) <= radius)
    wrecked = [c for _, c in near[:rng.randint(int(lo), int(hi))]]
    for cell in wrecked:
        set_cell(state, cell, DESTROYED[world.transport_at(*cell)])
    invalidate_terrain_caches(state, wrecked)
    nation = state.player
    nation.adjust_population(-int(info.get("population", 0)))
    nation.adjust_morale(float(info.get("morale", -2)))
    if info.get("factory"):
        nation.timed_modifiers.append({"key": "factory_efficiency", "value": float(info["factory"]["value"]),
                                       "weeks_left": int(info["factory"]["weeks"]), "source": kind})
    if report is not None:
        report.log.append(f"DISASTER: {info['name'].upper()} in {region.name} — {len(wrecked)} sections of rail/road wrecked.")
    card = disaster_card(state, kind, region.name, center, wrecked)
    raise_emergency(state, card)
    return card["id"]


def relief_candidate(state: GameState, center: tuple[int, int]) -> Unit | None:
    from src.engine.electronic_warfare import is_dark

    units = [u for u in state.player.units if state.domain(u) == "land" and not u.engaged and not u.routing
             and not u.relief_weeks and not is_dark(state, u) and state.role(u) != "hq"]
    return min(units, key=lambda u: (scaled_distance(state, u.location, center), u.id)) if units else None


def disaster_card(state: GameState, kind: str, region: str, center: tuple[int, int], wrecked: list) -> dict:
    cfg = _cfg(state)
    info = cfg["disasters"][kind]
    text = fill(info.get("text", ""), state.text_vars() | {"region": region})
    text += (f"\n\nDAMAGE: {len(wrecked)} sections of railway and road wrecked around grid {center[0]:03d}-{center[1]:03d} — "
             f"supply columns must detour until Combat Engineers rebuild them. "
             f"CASUALTIES: about {int(info.get('population', 0)):,} civilians dead or missing.\n\n"
             "The provincial governor begs for help. The General Staff begs you not to weaken the front.")
    choices = [{"id": "relief", "label": "Fund Relief",
                "hint": "Relief trains, field kitchens, tents and medicine for the survivors.",
                "effects": {"treasury": -int(cfg.get("relief_cost", 50000)), "morale": cfg.get("relief_morale", 2)}}]
    unit = relief_candidate(state, center)
    if unit is not None:
        weeks = int(cfg.get("military_weeks", 3))
        choices.append({"id": "military", "label": f"Deploy the Military ({unit.designation})",
                        "hint": f"The {unit.name} is pulled off the line to dig out survivors: it cannot move or fight "
                                f"for {weeks} weeks.",
                        "effects": {"relief_unit": {"unit": unit.id, "weeks": weeks},
                                    "morale": cfg.get("military_morale", 4)}})
    choices.append({"id": "ignore", "label": "Ignore",
                    "hint": "The war comes first. The people will not forget.",
                    "effects": {"morale": cfg.get("ignore_morale", -8)}})
    return {"id": f"crisis_{kind}_{state.clock.turn}_{center[0]}_{center[1]}",
            "title": f"CRITICAL EMERGENCY: {info['name'].upper()} IN {region.upper()}",
            "category": "NATURAL DISASTER", "emergency": True, "text": text, "choices": choices}


# --- emergencies & relief duty -------------------------------------------------------------------------------


def raise_emergency(state: GameState, card: dict) -> None:
    """Show a CRITICAL EMERGENCY modal now, or after the one already on screen."""
    state.crisis_cards[card["id"]] = card
    if state.pending_dilemma is None:
        state.pending_dilemma = card["id"]
    else:
        state.dilemma_queue.append(card["id"])


def assign_relief(state: GameState, unit_id: str, weeks: int) -> Unit | None:
    unit = state.unit(unit_id)
    if unit is None:
        return None
    unit.relief_weeks = max(unit.relief_weeks, int(weeks))
    unit.active_order = None
    unit.move_points = 0.0
    unit.pending_orders = []
    return unit


def require_available(state: GameState, unit: Unit) -> None:
    if unit.relief_weeks > 0:
        raise OrderError(f"{unit.designation} is on disaster relief duty for {unit.relief_weeks} more week(s).")


def run_relief(state: GameState, report: TickReport) -> None:
    for unit in state.player.units:
        if unit.relief_weeks > 0:
            unit.relief_weeks -= 1
            if unit.relief_weeks == 0:
                report.log.append(f"{unit.designation} has finished relief work and returns to duty.")


def health_summary(state: GameState) -> list[str]:
    lines = []
    for outbreak, sites in outbreaks(state).items():
        inf = state.infections[sites[0]]
        status = "QUARANTINED" if inf["cure_in"] is not None else ("CORDONED" if inf["cordon"] else "UNCHECKED")
        lines.append(f"{disease(state, inf['disease'])['name']}: {len(sites)} site(s), {status}")
    return lines


class CrisisSystem(SimulationSystem):
    """The home front: epidemics run and spread, relief duty ends, disasters strike."""

    name = "crises"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        if state.game_over:
            return
        run_relief(state, report)
        forced = state.flags.pop("forced_crisis", None)  # main.py --crisis outbreak | disaster
        if forced == "outbreak":
            start_outbreak(state)
        elif forced == "disaster":
            start_disaster(state, report=report)
        run_epidemics(state, report)
        cfg = _cfg(state)
        if state.clock.turn >= int(cfg.get("first_turn", 4)) and state.rng.random() < float(cfg.get("disaster_chance", 0.05)):
            start_disaster(state, report=report)
        if state.pending_dilemma and state.pending_dilemma in state.crisis_cards:
            report.dilemma = state.pending_dilemma
