"""Recruitment & Training: raising new formations.

Raising a formation (any template in units.json) costs its `recruit_cost` from the treasury and
its `manpower` from the national pool, both paid up front, then takes `training_weeks`. When
training completes the formation musters at the capital (or the nation's `muster_point`) with full
manpower, green-troop morale (recruitment.initial_morale) and only `initial_issue` of its
establishment, drawn from the national stockpile. It is NOT combat-ready: the logistics pipeline
must fill its inventory from the stockpile over the following weeks.

Cancelling a formation in training refunds all its manpower and `cancel_refund` of its cost.
AI nations raise replacements when their army falls below `ai_strength_target` of its starting
strength and they can afford it.
"""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.movement import passable
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import DEFEND, Email, GameState, Nation, TrainingOrder, Unit


class RecruitmentError(ValueError):
    """An invalid recruitment order."""


def _cfg(state: GameState) -> dict:
    return state.config.get("recruitment", {})


def templates(state: GameState) -> dict[str, dict]:
    return {t["id"]: t for t in state.catalog["units"]["units"]}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def muster_point(state: GameState, nation: Nation) -> tuple[int, int]:
    if nation.muster_point and passable(state, *nation.muster_point):
        return nation.muster_point
    world = state.world_map
    capital = next((f for f in world.features if f.type == "capital" and world.owner_at(f.x, f.y) == nation.id), None)
    if capital and passable(state, capital.x, capital.y):
        return capital.x, capital.y
    return nation.units[0].location if nation.units else (world.width // 2, world.height // 2)


def _next_name(state: GameState, nation: Nation, template: dict) -> str:
    """'4th Kestrian Infantry Division': the lowest number not already carried by a formation."""
    capital = nation.capital or nation.name
    taken = {u.name for u in state.all_units()} | {o.name for o in state.training}
    pattern = template.get("name_pattern", "{ordinal} " + template["name"])
    n = 1
    while True:
        name = pattern.format(ordinal=_ordinal(n), adjective=nation.adjective, capital=capital)
        if name not in taken:
            return name
        n += 1


def _next_designation(state: GameState, nation: Nation) -> str:
    prefix = nation.id[0].upper()
    taken = {u.designation for u in state.all_units()} | {o.designation for o in state.training}
    n = 1
    while f"{prefix}-{n:02d}" in taken:
        n += 1
    return f"{prefix}-{n:02d}"


def raise_formation(state: GameState, nation_id: str, template_id: str) -> TrainingOrder:
    if state.game_over:
        raise RecruitmentError("The government has fallen. The terminal is locked.")
    nation = state.nations[nation_id]
    table = templates(state)
    if template_id not in table:
        raise RecruitmentError(f"Unknown formation type: {template_id}")
    template = table[template_id]
    cost, men = int(template["recruit_cost"]), int(template["manpower"])
    if nation.treasury < cost:
        raise RecruitmentError(f"The treasury cannot fund a {template['name']} ({cost:,} {state.currency} needed).")
    if nation.manpower < men:
        raise RecruitmentError(f"Not enough manpower: a {template['name']} needs {men:,} men, the pool holds {nation.manpower:,}.")
    nation.adjust_treasury(-cost)
    nation.adjust_manpower(-men)
    state.unit_serial += 1
    order = TrainingOrder(
        id=f"{nation_id}_{template_id}_{state.unit_serial:03d}",
        nation_id=nation_id,
        unit_type=template_id,
        designation=_next_designation(state, nation),
        name=_next_name(state, nation, template),
        weeks_total=int(template["training_weeks"]),
        weeks_left=int(template["training_weeks"]),
        cost=cost,
        manpower=men,
        started_turn=state.clock.turn,
    )
    state.training.append(order)
    return order


def cancel_training(state: GameState, order_id: str, nation_id: str | None = None) -> TrainingOrder:
    order = next((o for o in state.training if o.id == order_id), None)
    if order is None or order.nation_id != (nation_id or state.player.id):
        raise RecruitmentError(f"No such formation in training: {order_id}")
    nation = state.nations[order.nation_id]
    nation.adjust_manpower(order.manpower)
    nation.adjust_treasury(int(order.cost * float(_cfg(state).get("cancel_refund", 0.5))))
    state.training.remove(order)
    return order


def muster(state: GameState, order: TrainingOrder) -> Unit:
    """Training complete: the formation appears at the muster point, under-equipped."""
    from src.engine.logistics_engine import establishment  # avoid import cycle

    nation = state.nations[order.nation_id]
    template = templates(state)[order.unit_type]
    unit = Unit(
        id=order.id,
        designation=order.designation,
        name=order.name,
        nation_id=order.nation_id,
        unit_type=order.unit_type,
        location=muster_point(state, nation),
        strength=int(template["manpower"]),
        morale=float(_cfg(state).get("initial_morale", 60)),
        supply=100.0,
        stance=DEFEND,
    )
    share = float(_cfg(state).get("initial_issue", 0.25))
    for item_id, wanted in establishment(state, unit).items():
        take = min(int(wanted * share), nation.national_stockpile.get(item_id, 0))
        if take > 0:
            nation.national_stockpile[item_id] -= take
            unit.equipment_inventory[item_id] = take
    nation.units.append(unit)
    return unit


def ready_email(state: GameState, unit: Unit) -> Email:
    from src.engine.logistics_engine import fill_ratio

    tpl = state.catalog["generated"]["formation_ready"]
    region = state.world_map.region_at(*unit.location)
    body = "\n".join([
        f"{unit.name.upper()} ({unit.designation}) has completed training and reported for duty at grid "
        f"{unit.x:03d}-{unit.y:03d} ({region.name if region else 'the muster area'}).",
        "",
        f"Strength: {unit.strength:,} men. Morale: {unit.morale:.0f}% (green troops).",
        f"Ammunition on hand: {fill_ratio(unit=unit, state=state):.0%} of establishment. "
        "Weapons, ammunition and fuel will be drawn from the national stockpile over the coming weeks.",
        "",
        "Recommend the formation remain in the rear until its loadout is complete.",
        "",
        "— Adjutant-General's Office",
    ])
    variables = state.text_vars() | {"unit": unit.name}
    return Email(id="formation_ready", sender=fill(tpl["sender"], variables), subject=fill(tpl["subject"], variables),
                 classification=tpl.get("classification", "CONFIDENTIAL"), body=body)


def _ai_recruit(state: GameState, nation: Nation) -> None:
    cfg = _cfg(state)
    ai = state.ai_states[nation.id]
    queued = [o for o in state.training if o.nation_id == nation.id]
    if len(queued) >= int(cfg.get("ai_max_queue", 1)):
        return
    strength = sum(u.strength for u in nation.units) + sum(o.manpower for o in queued)
    if strength >= ai.baseline_strength * float(cfg.get("ai_strength_target", 0.95)):
        return
    choice = "armored_brigade" if state.rng.random() < 0.3 else "infantry_division"
    template = templates(state)[choice]
    if nation.treasury < template["recruit_cost"] * float(cfg.get("ai_treasury_reserve", 2.0)):
        return
    try:
        raise_formation(state, nation.id, choice)
    except RecruitmentError:
        pass


class RecruitmentSystem(SimulationSystem):
    """Advances training; completed formations muster before production and logistics run."""

    name = "recruitment"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        for nation_id, nation in state.nations.items():
            if nation_id in state.ai_states:
                _ai_recruit(state, nation)
        for order in list(state.training):
            order.weeks_left -= 1
            if order.weeks_left > 0:
                continue
            state.training.remove(order)
            unit = muster(state, order)
            if state.is_friendly(unit.nation_id):
                report.new_messages.append(deliver(state, ready_email(state, unit)))
                report.log.append(f"{unit.designation} {unit.name} has mustered.")
