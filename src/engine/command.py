"""Chain of command: commanders with hidden traits, insubordination, relieving a general.

Every formation's commander carries hidden TRAITS (data/commanders.json): Cautious, Aggressive,
Logistics-Master, Glory-Hound, Steady. The player learns them only when they show: a refusal, or the
commander's first week in battle (the SITREP carries a staff assessment).

Orders from the terminal are not obeyed instantly. A stance change or move order is queued on the unit
(`pending_orders`) and ACKNOWLEDGED at the start of the next week by the CommandSystem, which may roll a
REFUSAL:
  * ASSAULT without fire support (no friendly artillery in support range, no warship on BOMBARD in range,
    no friendly air wing over the sector): Cautious commanders refuse with `refuse_unsupported_assault`
    (80%); anyone may refuse with base_refusal, much more when the army's military morale is low.
  * WITHDRAW: Aggressive and Glory-Hound commanders may refuse to give ground (`refuse_withdraw`).
A refusal halts the order (the move order is cancelled), the formation reverts to DEFEND, the trait is
revealed and a COMMAND INSUBORDINATION dispatch arrives.

Traits also act in battle: attack / defense bonuses, casualties taken, supply consumption and resupply.

RELIEVING a commander (Military screen, F) installs a new general from the pool, with new hidden traits,
at a heavy cost in military morale (the officer corps closes ranks) and in the formation's own morale.
AI formations have commanders and traits too, but the enemy's generals obey their own staff.
"""

from __future__ import annotations

from src.engine.event_manager import GameOverError, deliver
from src.engine.movement import OrderError, scaled_distance
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import ASSAULT, DEFEND, ENGAGED, HOLDING, WITHDRAW, BOMBARD, Email, GameState, Unit

STEADY = "steady"


class CommandError(ValueError):
    """An invalid command decision (e.g. relieving a commander who cannot be reached)."""


def _data(state: GameState) -> dict:
    return state.catalog.get("commanders", {})


def trait_info(state: GameState, trait: str) -> dict:
    return _data(state).get("traits", {}).get(trait, {"name": trait.replace("_", " ").title()})


def trait_value(state: GameState, unit: Unit, key: str, default: float) -> float:
    """Combine a numeric trait effect over the commander's traits (multiplicative for factors, else additive)."""
    values = [float(trait_info(state, t)[key]) for t in unit.traits if key in trait_info(state, t)]
    if not values:
        return default
    if default == 1.0:
        result = 1.0
        for v in values:
            result *= v
        return result
    return sum(values)


def trait_names(state: GameState, unit: Unit) -> str:
    return ", ".join(trait_info(state, t)["name"].upper() for t in unit.traits) or "STEADY"


def roll_traits(state: GameState, rng=None) -> list[str]:
    rng = rng or state.rng
    weights = _data(state).get("trait_weights", {STEADY: 1})
    ids = sorted(weights)
    return [rng.choices(ids, weights=[weights[i] for i in ids], k=1)[0]]


def assign_commanders(state: GameState) -> None:
    """At the start of a campaign: known commanders get their listed traits, the rest roll them."""
    table = _data(state).get("orbat", {})
    import random

    rng = random.Random(f"{state.intel_seed}:commanders")  # independent of the campaign RNG
    for unit in state.all_units():
        if not unit.traits:
            unit.traits = list(table.get(unit.commander) or roll_traits(state, rng))


def next_commander(state: GameState, nation_id: str) -> str:
    taken = {u.commander for u in state.all_units()}
    pool = _data(state).get("pool", {}).get(nation_id, [])
    free = [name for name in pool if name not in taken]
    if free:
        return state.rng.choice(free)
    return f"Col. {state.rng.choice(['Ansel', 'Brun', 'Corve', 'Dace', 'Eld'])} {state.rng.randint(100, 999)}"


# --- orders from the terminal ------------------------------------------------------------------


def queue_order(state: GameState, unit: Unit, kind: str) -> None:
    """Record that the player gave this formation an order it has not yet acknowledged."""
    if state.is_friendly(unit.nation_id) and kind not in unit.pending_orders:
        unit.pending_orders.append(kind)


def fire_support(state: GameState, unit: Unit) -> list[str]:
    """What heavy fire would back this formation this week: artillery, naval gunfire, air."""
    support = []
    combat = state.config.get("combat", {})
    reach = float(combat.get("artillery_support_range", 2.5))
    nation = state.nations[unit.nation_id]
    for gun in nation.units:
        if gun.id != unit.id and state.template(gun.unit_type).get("support") and not gun.routing \
                and scaled_distance(state, gun.location, unit.location) <= reach:
            support.append(f"artillery ({gun.designation})")
            break
    naval_reach = float(state.config.get("naval", {}).get("bombard_range", 2.5))
    for ship in nation.units:
        if state.domain(ship) == "sea" and ship.mission == BOMBARD and \
                scaled_distance(state, ship.location, unit.location) <= naval_reach:
            support.append(f"naval gunfire ({ship.designation})")
            break
    region = state.world_map.region_at(*unit.location)
    if region is not None and any(w.nation_id == unit.nation_id and w.sector == region.id and w.aircraft > 0
                                  for w in state.air_wings):
        support.append("air cover")
    return support


def refusal_chance(state: GameState, unit: Unit, order: str) -> tuple[float, str]:
    """(chance, reason) that the commander refuses the order he has just received."""
    data = _data(state)
    nation = state.nations[unit.nation_id]
    if "royal" in unit.traits:  # a member of the ruling House obeys the Lord Protector to the letter
        return 0.0, ""
    if order == ASSAULT:
        if fire_support(state, unit):
            return 0.0, ""
        chance = float(data.get("base_refusal", 0.05))
        if nation.military_morale < float(data.get("low_military_morale", 30)):
            chance += float(data.get("low_morale_refusal", 0.25))
        chance = max(chance, trait_value(state, unit, "refuse_unsupported_assault", 0.0))
        return min(1.0, chance), "refuses to advance without heavy fire support"
    if order == WITHDRAW:
        return min(1.0, trait_value(state, unit, "refuse_withdraw", 0.0)), "refuses to give ground"
    return 0.0, ""


def insubordination_email(state: GameState, unit: Unit, order: str, reason: str) -> Email:
    tpl = state.catalog["generated"]["insubordination"]
    assessment = "; ".join(trait_info(state, t).get("assessment", "") for t in unit.traits)
    ordered = {"assault": "go over to the ASSAULT", "withdraw": "WITHDRAW"}.get(order, order.upper())
    lines = [
        "FLASH — FOR THE LORD PROTECTOR ONLY.",
        "",
        f"{unit.commander}, commanding the {unit.name} ({unit.designation}), {reason}.",
        "",
        f"Your order to {ordered} was received at divisional headquarters and not executed. The formation has halted "
        "and reverted to DEFEND. Standing movement orders are cancelled.",
        "",
        f"STAFF ASSESSMENT: {assessment or 'no prior record of disobedience'}.",
        f"Commander's disposition: {trait_names(state, unit)}.",
        "",
        "You may repeat the order next week, support the attack with artillery (1-2 cells behind the line), naval "
        "gunfire or air cover, or RELIEVE the commander (Military screen, F) at a cost to the army's loyalty.",
        "",
        "— Chief of the General Staff",
    ]
    variables = state.text_vars() | {"designation": unit.designation, "commander": unit.commander.upper()}
    return Email(id="command_insubordination", sender=fill(tpl["sender"], variables),
                 subject=fill(tpl["subject"], variables), classification=tpl.get("classification", "TOP SECRET"),
                 body="\n".join(lines))


def acknowledge_orders(state: GameState, report: TickReport) -> list[Unit]:
    """Start of the week: every order given since last week is obeyed, or refused. Returns refusers."""
    refused = []
    for unit in list(state.player.units):
        pending, unit.pending_orders = unit.pending_orders, []
        if not pending or unit.routing:
            continue
        chance, reason = refusal_chance(state, unit, unit.stance)
        if chance <= 0 or state.rng.random() >= chance:
            continue
        order = unit.stance
        unit.stance = DEFEND
        unit.active_order = None
        unit.move_points = 0.0
        if unit.status != ENGAGED:
            unit.status = HOLDING
        unit.traits_known = True
        refused.append(unit)
        report.new_messages.append(deliver(state, insubordination_email(state, unit, order, reason)))
        report.log.append(f"INSUBORDINATION: {unit.designation} {unit.commander} {reason}.")
    return refused


# --- relieving a commander ------------------------------------------------------------------------


def relieve_commander(state: GameState, unit_id: str, *, nation_id: str | None = None) -> tuple[str, str]:
    """Fire the commander and install a new one. Returns (old name, new name)."""
    from src.engine.electronic_warfare import require_signal

    if state.game_over:
        raise GameOverError("The government has fallen. The terminal is locked.")
    unit = state.unit(unit_id)
    issuer = nation_id or state.player.id
    if unit is None or unit.nation_id != issuer:
        raise CommandError(f"No such formation under your command: {unit_id}")
    try:
        require_signal(state, unit)
    except OrderError as error:
        raise CommandError(str(error)) from None
    cost = _data(state).get("relieve", {})
    from src.engine.court import royal_of

    royal = royal_of(state, unit)
    if royal is not None:  # a relieved royal goes home to court
        royal.unit_id = None
    old = unit.commander
    unit.commander = next_commander(state, unit.nation_id)
    unit.traits = roll_traits(state)
    unit.traits_known = False
    unit.pending_orders = []
    nation = state.nations[unit.nation_id]
    nation.adjust_military_morale(float(cost.get("military_morale", -6)))
    unit.morale = max(0.0, unit.morale + float(cost.get("unit_morale", -8)))
    if state.is_friendly(unit.nation_id):
        tpl = state.catalog["generated"]["command_change"]
        variables = state.text_vars() | {"designation": unit.designation, "unit": unit.name}
        body = "\n".join([
            f"By order of the {state.player.leader_title}, {old} is relieved of command of the {unit.name} "
            f"({unit.designation}) with immediate effect and recalled to Aldmark.",
            "",
            f"{unit.commander} assumes command. The staff has no assessment of the new commander yet.",
            "",
            f"The officer corps takes the dismissal badly: military morale {cost.get('military_morale', -6):+g}, "
            f"formation morale {cost.get('unit_morale', -8):+g}.",
            "",
            "— Office of the Chief of the General Staff",
        ])
        delivered = deliver(state, Email(id="command_change", sender=fill(tpl["sender"], variables),
                                         subject=fill(tpl["subject"], variables),
                                         classification=tpl.get("classification", "SECRET"), body=body))
        delivered.read = True
    return old, unit.commander


def assessment_line(state: GameState, unit: Unit) -> str:
    """Revealed on a commander's first week in battle (called from the SITREP)."""
    unit.traits_known = True
    text = "; ".join(trait_info(state, t).get("assessment", "") for t in unit.traits)
    return f"{unit.designation} {unit.commander}: {trait_names(state, unit)} — {text}"


class CommandSystem(SimulationSystem):
    """Start of the week: commanders acknowledge (or refuse) the orders given since last week."""

    name = "command"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        acknowledge_orders(state, report)
