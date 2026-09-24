"""The Military Industrial Complex: factories -> finished equipment -> national stockpile.

Each nation owns `military_factories`. The player assigns them to production lines, one line per
equipment id in data/equipment.json (only items whose `requires_tech` is known). Every week each
assigned factory produces `batch * 7 / factory_days * efficiency` units, limited by the raw
resources in `cost` (steel, munitions, fuel ... from the nation's resource stockpiles). A new line
starts at `production.start_efficiency` and improves by `efficiency_gain` per week of continuous
running; closing a line down resets it. Output is deposited in `nation.national_stockpile`, from
which the logistics pipeline resupplies formations in the field.

Domestic resource output (`nation.resource_output`, a placeholder economy) is added each week
before factories draw their inputs. AI nations re-balance their own factories toward whatever
their formations are short of.
"""

from __future__ import annotations

import math

from src.engine.systems import SimulationSystem, TickReport
from src.models import GameState, Nation


class ProductionError(ValueError):
    """An invalid factory assignment."""


def _cfg(state: GameState) -> dict:
    return state.config.get("production", {})


def equipment_by_id(state: GameState) -> dict[str, dict]:
    return {e["id"]: e for e in state.catalog["equipment"]}


def is_unlocked(nation: Nation, item: dict) -> bool:
    return item.get("requires_tech") is None or item["requires_tech"] in nation.known_techs


def weekly_rate(state: GameState, item: dict) -> float:
    """Units one factory makes per week at 100% efficiency."""
    return float(item["batch"]) * 7.0 / float(item["factory_days"])


def assign_factories(state: GameState, nation_id: str, item_id: str, delta: int) -> int:
    """Add (delta > 0) or remove (delta < 0) factories on a line. Returns the new count."""
    nation = state.nations[nation_id]
    items = equipment_by_id(state)
    if item_id not in items:
        raise ProductionError(f"Unknown equipment: {item_id}")
    item = items[item_id]
    if delta > 0 and not is_unlocked(nation, item):
        raise ProductionError(f"{item['name']} requires research: {item['requires_tech']}.")
    current = nation.production.get(item_id, 0)
    if delta > 0 and nation.free_factories < delta:
        raise ProductionError("No free military factories. Take factories off another line first.")
    new = max(0, current + delta)
    if new == 0:
        nation.production.pop(item_id, None)
        nation.line_efficiency.pop(item_id, None)  # the line is dismantled
    else:
        if current == 0:
            nation.line_efficiency[item_id] = float(_cfg(state).get("start_efficiency", 0.5))
        nation.production[item_id] = new
    return new


def forecast(state: GameState, nation: Nation, item_id: str) -> float:
    """Planned weekly output of a line at its current efficiency (ignoring input shortages)."""
    item = equipment_by_id(state)[item_id]
    bonus = 1.0 + nation.modifiers.get("factory_efficiency", 0.0)  # e.g. Assembly-Line Retooling
    return nation.production.get(item_id, 0) * weekly_rate(state, item) * nation.line_efficiency.get(item_id, 0.0) * bonus


def run_production(state: GameState, nation: Nation) -> dict:
    """One week of domestic output and factory production. Returns a report dict."""
    cfg = _cfg(state)
    items = equipment_by_id(state)
    for resource, amount in nation.resource_output.items():
        nation.adjust_stockpile(resource, amount)

    produced: dict[str, int] = {}
    shortages: dict[str, set[str]] = {}
    for item_id, factories in sorted(nation.production.items()):
        item = items[item_id]
        planned = forecast(state, nation, item_id)
        feasible = planned
        for resource, per_unit in item.get("cost", {}).items():
            if per_unit > 0:
                affordable = nation.stockpiles.get(resource, 0) / per_unit
                if affordable < feasible:
                    feasible = affordable
                    shortages.setdefault(item_id, set()).add(resource)
        made = int(math.floor(max(0.0, feasible)))
        for resource, per_unit in item.get("cost", {}).items():
            nation.adjust_stockpile(resource, -math.ceil(made * per_unit))
        if made:
            nation.national_stockpile[item_id] = nation.national_stockpile.get(item_id, 0) + made
        produced[item_id] = made
        gain = float(cfg.get("efficiency_gain", 0.1))
        nation.line_efficiency[item_id] = min(1.0, nation.line_efficiency.get(item_id, 0.5) + gain)
    report = {"produced": produced, "shortages": {k: sorted(v) for k, v in shortages.items()}}
    state.last_production[nation.id] = report
    return report


def demand(state: GameState, nation: Nation) -> dict[str, int]:
    """What the nation's formations are short of, per item (establishment minus inventory)."""
    from src.engine.logistics_engine import establishment  # avoid import cycle

    needs: dict[str, int] = {}
    for unit in nation.units:
        for item_id, wanted in establishment(state, unit).items():
            gap = wanted - unit.equipment_inventory.get(item_id, 0)
            if gap > 0:
                needs[item_id] = needs.get(item_id, 0) + gap
    return needs


def ai_rebalance(state: GameState, nation: Nation) -> None:
    """AI nations point their factories at their worst shortages (weighted by factory-weeks needed)."""
    items = equipment_by_id(state)
    needs = demand(state, nation)
    weights = {}
    for item_id, gap in needs.items():
        item = items.get(item_id)
        if item is None or not is_unlocked(nation, item):
            continue
        in_stock = nation.national_stockpile.get(item_id, 0)
        shortfall = max(0, gap - in_stock) + 0.25 * gap  # keep a trickle even when depots are full
        weights[item_id] = shortfall / weekly_rate(state, item)
    total = sum(weights.values())
    if total <= 0:
        return
    target = {k: int(round(nation.military_factories * w / total)) for k, w in weights.items()}
    while sum(target.values()) > nation.military_factories:
        biggest = max(target, key=target.get)
        target[biggest] -= 1
    for item_id in list(nation.production):
        if target.get(item_id, 0) == 0:
            assign_factories(state, nation.id, item_id, -nation.production[item_id])
    for item_id, count in target.items():
        delta = count - nation.production.get(item_id, 0)
        if delta < 0:
            assign_factories(state, nation.id, item_id, delta)
    for item_id, count in target.items():
        delta = count - nation.production.get(item_id, 0)
        if delta > 0:
            assign_factories(state, nation.id, item_id, min(delta, nation.free_factories))


class ProductionSystem(SimulationSystem):
    """Runs before logistics so this week's output can ship to the front this week."""

    name = "production"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        for nation_id, nation in state.nations.items():
            if nation_id in state.ai_states:
                ai_rebalance(state, nation)
            result = run_production(state, nation)
            if state.is_friendly(nation_id) and result["shortages"]:
                items = equipment_by_id(state)
                names = ", ".join(items[i]["name"] for i in result["shortages"])
                report.log.append(f"Production short of raw materials: {names}.")
