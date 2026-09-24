"""The Royal Court & Extended Family (Expansion 1.2).

The player's nation is ruled by a DYNASTY (Nation.dynasty): the Lord Protector, the family (data/dynasty.json, the
Head Writer's cast, plus generated siblings and cousins) and two or three powerful nobles. Every Character has
Administration / Military / Intrigue (0-10), traits, loyalty and influence. data/court.json holds the numbers.

THE CABINET. Minister of Finance (Administration: tax revenue), Minister of War (Military: military morale each
week) and Head of Intelligence (Intrigue: recon accuracy, and the chance to catch a plot before it strikes).
Trait bonuses count too: a Financial Genius counts 3 points better at Finance; a Corrupt minister skims the taxes.

HOLDING COURT. Every few weeks a courtier demands an audience (a modal card): granting raises their loyalty,
denying or ignoring lowers it. The Lord Protector can also hold court at will (Royal Court screen, A).

TREASON. A character whose loyalty falls below 20 plots: embezzlement, leaking war plans to the Vosk, an attempt
on the Lord Protector's life or, for an ambitious and influential member of the blood, a COUP. The Head of
Intelligence may uncover the plot first: execute, imprison or pardon the traitor.

MARRIAGE. An unmarried member of the House can be married into Oakhaven, Tor or Vael: the power swings +20
toward Kestria, never falls below +20 while the marriage holds, and sells lend-lease 25% cheaper.

FAMILY GENERALS. A royal can command a division (Military screen, K): royals never disobey, but a royal killed
in action costs the House 20 stability.

SUCCESSION. Everyone ages; the old die, the plague in the capital reaches the palace, assassins strike. When the
ruler dies the Heir takes the oath and play goes on under the new ruler's traits. With nobody of the blood left,
or after a successful coup: PROTOCOL ZERO: DYNASTIC COLLAPSE (a fail state).
"""

from __future__ import annotations

import random
import re

from src.engine.event_manager import GameOverError, deliver
from src.engine.systems import SimulationSystem, TickReport
from src.engine.text import fill
from src.models import ENGAGED, Email, GameState, Nation, Unit
from src.models.character import (
    BLOOD_RELATIONS,
    COUSIN,
    DOWAGER,
    HEIR,
    NOBLE,
    OFFICES,
    RULER,
    SIBLING,
    SPOUSE,
    UNCLE_AUNT,
    Character,
    Dynasty,
)

STAT_NAMES = {"admin": "administration", "administration": "administration", "military": "military",
              "intrigue": "intrigue"}


class CourtError(ValueError):
    """An invalid decision at court (appointing the dead, marrying a married prince, ...)."""


def cfg(state: GameState) -> dict:
    return state.catalog.get("court", {})


def dynasty(state: GameState) -> Dynasty | None:
    return state.player.dynasty


def trait_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def trait_info(state: GameState, trait: str) -> dict:
    return cfg(state).get("traits", {}).get(trait, {"name": trait.replace("_", " ").title()})


def trait_sum(state: GameState, char: Character, key: str) -> float:
    return sum(float(trait_info(state, t).get(key, 0.0)) for t in char.traits)


def trait_names(state: GameState, char: Character) -> str:
    return ", ".join(trait_info(state, t)["name"] for t in char.traits) or "—"


def office_name(state: GameState, office: str) -> str:
    return cfg(state).get("cabinet", {}).get(office, {}).get("name", office.title())


def display_name(char: Character) -> str:
    return f"Lord Protector {char.name}" if char.relation == RULER else char.name


def _require(state: GameState) -> Dynasty:
    if state.game_over:
        raise GameOverError("The government has fallen. The terminal is locked.")
    house = dynasty(state)
    if house is None:
        raise CourtError("This nation has no ruling house.")
    return house


def character(state: GameState, char_id: str) -> Character:
    house = _require(state)
    if char_id not in house.characters:
        raise CourtError(f"No such courtier: {char_id}")
    return house.characters[char_id]


# --- the starting court -------------------------------------------------------------------------


def _relation(raw: str) -> str:
    text = raw.strip().lower()
    for relation in (HEIR, SPOUSE, SIBLING, COUSIN, NOBLE, DOWAGER):
        if text.startswith(relation.lower()):
            return relation
    if text.startswith(("uncle", "aunt")):
        return UNCLE_AUNT
    raise ValueError(f"dynasty.json: unknown relation {raw!r}")


def _traits(state: GameState, names: list[str], where: str) -> list[str]:
    ids = [trait_id(n) for n in names]
    unknown = [n for n, i in zip(names, ids) if i not in cfg(state).get("traits", {})]
    if unknown:
        raise ValueError(f"{where}: traits {unknown} have no mechanics in court.json (add them to 'traits')")
    return ids


def _generate(state: GameState, rng: random.Random, relation: str, index: int, used: set[str]) -> Character:
    data = cfg(state)
    spec = data["generate"][relation]
    names = data["names"]
    stats = data["stats"]
    weights = data["noble_trait_weights" if relation == NOBLE else "trait_weights"]
    keys = sorted(weights)
    trait = rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]
    traits = [] if trait == "none" else [trait]
    if relation == NOBLE:
        houses = [h for h in names["noble_houses"] if h[0] not in used]
        house, role, style = rng.choice(houses)
        used.add(house)
        given = rng.choice([g for g, s in names["given"] if s == style and g not in used])
        used.add(given)
        name, char_id = f"{given} {house}", f"noble_{index}"
    else:
        given, style = rng.choice([g for g in names["given"] if g[0] not in used])
        used.add(given)
        name = f"{names['styles'][style]} {given}"
        role = rng.choice(names["roles"][relation])
        char_id = f"{relation.lower()}_{index}"
    bonus = int(trait_sum(state, Character("", "", 0, "", "", 0, 0, 0, traits=traits), "stat_bonus"))

    def stat() -> int:
        return min(int(stats["max"]), rng.randint(int(stats["low"]), int(stats["high"])) + bonus)

    char = Character(id=char_id, name=name, age=rng.randint(*spec["age"]), relation=relation, role=role,
                     administration=stat(), military=stat(), intrigue=stat(), traits=traits,
                     influence=float(rng.randint(*spec["influence"])), dynasty=relation != NOBLE)
    char.loyalty = round(loyalty_target(state, char, ruler_bonus=0.0) + rng.uniform(-8, 8), 1)
    return char


def init_court(state: GameState) -> None:
    """Seat the House: the canon cast from dynasty.json, generated relatives and nobles, the starting cabinet."""
    data = cfg(state)
    cast = state.catalog.get("dynasty")
    if not data or not cast:
        return
    extra = data.get("cast", {})
    house = Dynasty(name=data.get("dynasty_name", "The Ruling House"),
                    stability=float(data.get("stability", {}).get("start", 60)))
    raw = cast["ruler"]
    ruler_extra = extra.get("ruler", {})
    ruler = Character(id=ruler_extra.get("id", "ruler"), name=re.sub(r"^Lord Protector\s+", "", raw["name"]),
                      age=int(raw.get("age", 55)), relation=RULER, role=raw.get("title", "Lord Protector"),
                      administration=int(ruler_extra.get("administration", 5)),
                      military=int(ruler_extra.get("military", 5)), intrigue=int(ruler_extra.get("intrigue", 5)),
                      traits=_traits(state, raw.get("traits", []), "dynasty.json ruler"), loyalty=100.0,
                      influence=float(ruler_extra.get("influence", 90)))
    house.characters[ruler.id] = ruler
    house.ruler_id = ruler.id
    for member in cast.get("family", []):
        more = extra.get(member["id"], {})
        relation = _relation(member["relation"])
        stats = {STAT_NAMES[k]: int(v) for k, v in member.get("stats", {}).items()}
        char = Character(id=member["id"], name=member["name"], age=int(more.get("age", member.get("age", 40))),
                         relation=relation, role=more.get("role", member.get("role", relation)),
                         administration=stats.get("administration", 5), military=stats.get("military", 5),
                         intrigue=stats.get("intrigue", 5),
                         traits=_traits(state, member.get("traits", []), f"dynasty.json [{member['id']}]"),
                         loyalty=float(member.get("loyalty", 50)), influence=float(more.get("influence", 40)),
                         dynasty=relation not in (SPOUSE, NOBLE))
        house.characters[char.id] = char
    rng = random.Random(f"{state.intel_seed}:court")  # independent of the campaign RNG
    used = {c.name.split()[-1] for c in house.characters.values()}
    for relation in ("Sibling", "Cousin", "Noble"):
        spec = data.get("generate", {}).get(relation)
        if not spec:
            continue
        for index in range(1, rng.randint(int(spec["min"]), int(spec["max"])) + 1):
            char = _generate(state, rng, relation, index, used)
            house.characters[char.id] = char
    for office, char_id in data.get("starting_cabinet", {}).items():
        if char_id in house.characters:
            house.characters[char_id].office = office
    first = data.get("audiences", {}).get("first_turn", 3)
    house.next_audience_turn = state.clock.turn + int(first)
    state.player.dynasty = house


# --- loyalty and the cabinet --------------------------------------------------------------------


def ruler_trait(state: GameState, key: str) -> float:
    house = dynasty(state)
    return trait_sum(state, house.ruler, key) if house and house.ruler.alive else 0.0


def loyalty_target(state: GameState, char: Character, ruler_bonus: float | None = None) -> float:
    data = cfg(state).get("loyalty", {})
    house = dynasty(state)
    target = float(data.get("base", {}).get(char.relation, 50))
    target += trait_sum(state, char, "loyalty")
    if char.office:
        target += float(data.get("office_favour", 10))
    stability = house.stability if house else 60.0
    target += (stability - 50) * float(data.get("stability_factor", 0.2))
    target += ruler_trait(state, "court_loyalty") if ruler_bonus is None else ruler_bonus
    return max(0.0, min(100.0, target))


def adjust_loyalty(char: Character, delta: float) -> None:
    char.loyalty = max(0.0, min(100.0, char.loyalty + delta))


def minister(state: GameState, office: str, nation: Nation | None = None) -> Character | None:
    house = (nation or state.player).dynasty
    return house.minister(office) if house else None


def office_stat(state: GameState, office: str, nation: Nation | None = None) -> float:
    """The effective stat of the office holder (with trait bonuses), or the vacant value."""
    cabinet = cfg(state).get("cabinet", {})
    holder = minister(state, office, nation)
    if holder is None:
        return float(cabinet.get("vacant_stat", 2))
    bonus = sum(float(trait_info(state, t).get("office_bonus", {}).get(office, 0)) for t in holder.traits)
    return holder.stat(cabinet[office]["stat"]) + bonus


def office_delta(state: GameState, office: str, nation: Nation | None = None) -> float:
    return office_stat(state, office, nation) - float(cfg(state).get("cabinet", {}).get("pivot", 5))


def tax_adjustments(state: GameState, nation: Nation, tax: float) -> list[tuple[str, int]]:
    """Ledger lines from the court: (label, +income / -expense) for the given base tax revenue."""
    if nation.dynasty is None:
        return []
    cabinet = cfg(state).get("cabinet", {})
    lines = []
    holder = minister(state, "finance", nation)
    share = float(cabinet.get("finance", {}).get("tax_per_point", 0.03)) * office_delta(state, "finance", nation)
    who = f"{holder.name} (ADM {office_stat(state, 'finance', nation):g})" if holder else "VACANT"
    if share:
        lines.append((f"Minister of Finance: {who}", round(tax * share)))
    ruler_tax = trait_sum(state, nation.dynasty.ruler, "tax")
    if ruler_tax:
        lines.append((f"The Lord Protector's household ({trait_names(state, nation.dynasty.ruler)})", round(tax * ruler_tax)))
    if holder is not None:
        skim = trait_sum(state, holder, "embezzle")
        if skim:
            lines.append(("Unaccounted expenditure (Treasury)", -round(tax * skim)))
    return lines


def recon_bonus(state: GameState) -> float:
    if dynasty(state) is None:
        return 0.0
    per = float(cfg(state).get("cabinet", {}).get("intelligence", {}).get("recon_accuracy_per_point", 0.015))
    return per * office_delta(state, "intelligence") + state.player.modifier("recon_accuracy")


def research_bonus(state: GameState, nation: Nation) -> float:
    return trait_sum(state, nation.dynasty.ruler, "research_bonus") if nation.dynasty else 0.0


def detection_chance(state: GameState, plotter: Character) -> float:
    spec = cfg(state).get("cabinet", {}).get("intelligence", {})
    head = minister(state, "intelligence")
    if head is None:
        return float(spec.get("vacant_detection", 0.1))
    if head.id == plotter.id:
        return 0.0
    chance = float(spec.get("base_detection", 0.2)) + float(spec.get("plot_detection_per_point", 0.08)) * \
        office_delta(state, "intelligence") + trait_sum(state, head, "plot_detection")
    return max(0.0, min(0.95, chance))


def appoint(state: GameState, char_id: str, office: str) -> Character | None:
    """Give a courtier a cabinet office. Returns the minister replaced (if any)."""
    house = _require(state)
    if office not in OFFICES:
        raise CourtError(f"Unknown office {office!r}")
    char = character(state, char_id)
    if char.relation == RULER:
        raise CourtError("The Lord Protector does not serve in his own cabinet.")
    if not char.alive or char.imprisoned:
        raise CourtError(f"{char.name} cannot take office.")
    if char.married_to:
        raise CourtError(f"{char.name} lives abroad since the marriage.")
    if char.unit_id:
        raise CourtError(f"{char.name} commands a division in the field. Recall them first (Military screen, K).")
    if char.office == office:
        raise CourtError(f"{char.name} already holds that office.")
    data = cfg(state).get("loyalty", {})
    previous = house.minister(office)
    if previous is not None:
        previous.office = None
        adjust_loyalty(previous, float(data.get("dismissed", -15)))
    char.office = office
    adjust_loyalty(char, float(data.get("appointed", 10)))
    _history(state, f"{char.name} appointed {office_name(state, office)}"
                    + (f", replacing {previous.name}." if previous else "."))
    return previous


def dismiss(state: GameState, char_id: str) -> str:
    char = character(state, char_id)
    if not char.office:
        raise CourtError(f"{char.name} holds no office.")
    office = char.office
    char.office = None
    adjust_loyalty(char, float(cfg(state).get("loyalty", {}).get("dismissed", -15)))
    _history(state, f"{char.name} dismissed as {office_name(state, office)}.")
    return office


# --- family generals ------------------------------------------------------------------------------


def royal_of(state: GameState, unit: Unit) -> Character | None:
    house = dynasty(state)
    if house is None:
        return None
    return next((c for c in house.living() if c.unit_id == unit.id), None)


def eligible_generals(state: GameState) -> list[Character]:
    house = dynasty(state)
    if house is None:
        return []
    return sorted((c for c in house.living() if c.relation != RULER and c.at_court and c.dynasty),
                  key=lambda c: (-c.military, c.name))


def appoint_commander(state: GameState, char_id: str, unit_id: str) -> str:
    """A royal takes command of a friendly division. Returns the name of the general sent home."""
    _require(state)
    char = character(state, char_id)
    unit = state.unit(unit_id)
    if unit is None or not state.is_friendly(unit.nation_id) or state.domain(unit) != "land":
        raise CourtError("Royals command land formations of the Commonwealth.")
    if char.relation == RULER or not char.dynasty:
        raise CourtError(f"{char.name} is not a prince of the blood.")
    if not char.alive or char.imprisoned or char.married_to or char.unit_id:
        raise CourtError(f"{char.name} is not available for a field command.")
    current = royal_of(state, unit)
    if current is not None:
        recall_commander(state, unit.id)
    if char.office:
        char.office = None
    old = unit.commander
    unit.commander = char.name
    unit.traits = ["royal"] + [t for trait in char.traits for t in trait_info(state, trait).get("commander_traits", [])]
    unit.traits_known = True
    unit.pending_orders = []
    char.unit_id = unit.id
    _history(state, f"{char.name} takes command of the {unit.name} ({unit.designation}).")
    return old


def recall_commander(state: GameState, unit_id: str) -> Character:
    from src.engine.command import next_commander, roll_traits

    _require(state)
    unit = state.unit(unit_id)
    char = royal_of(state, unit) if unit else None
    if char is None:
        raise CourtError("No member of the House commands that formation.")
    char.unit_id = None
    unit.commander = next_commander(state, unit.nation_id)
    unit.traits = roll_traits(state)
    unit.traits_known = False
    _history(state, f"{char.name} recalled from the {unit.name}; {unit.commander} takes command.")
    return char


def frontline_unit(state: GameState) -> Unit | None:
    """The division nearest the enemy that no royal commands (for 'give him a vanguard division')."""
    from src.engine.movement import scaled_distance

    enemies = [u.location for u in state.hostile_units() if state.domain(u) == "land"]
    candidates = [u for u in state.player.units if state.domain(u) == "land" and state.role(u) != "hq"
                  and royal_of(state, u) is None and not u.routing]
    if not candidates:
        return None
    if not enemies:
        return max(candidates, key=lambda u: u.strength)
    return min(candidates, key=lambda u: (min(scaled_distance(state, u.location, e) for e in enemies), u.id))


# --- marriages ------------------------------------------------------------------------------------


def marriage_candidates(state: GameState) -> list[Character]:
    spec = cfg(state).get("marriage", {})
    house = dynasty(state)
    if house is None:
        return []
    return [c for c in house.living() if c.dynasty and c.relation != RULER and not c.married_to and not c.imprisoned
            and int(spec.get("min_age", 16)) <= c.age <= int(spec.get("max_age", 45))]


def marriage_partners(state: GameState) -> list[str]:
    """Powers that would take a Kestrian prince or princess now."""
    from src.engine.diplomacy import relation

    spec = cfg(state).get("marriage", {})
    return [nid for nid in sorted(state.foreign) if not state.foreign[nid].get("marriage")
            and relation(state, nid) >= float(spec.get("min_alignment", 0))]


def marry(state: GameState, char_id: str, power: str) -> list[str]:
    from src.engine.diplomacy import adjust_relation, nations

    _require(state)
    spec = cfg(state).get("marriage", {})
    char = character(state, char_id)
    if char not in marriage_candidates(state):
        raise CourtError(f"{char.name} cannot be married abroad.")
    if power not in marriage_partners(state):
        raise CourtError("That power will not take a Kestrian match now.")
    dowry = int(spec.get("dowry", 50000))
    if state.player.treasury < dowry:
        raise CourtError(f"The dowry is {dowry:,} {state.currency}.")
    state.player.adjust_treasury(-dowry)
    if char.office:
        char.office = None
    if char.unit_id:
        recall_commander(state, char.unit_id)
    char.married_to = power
    adjust_loyalty(char, float(spec.get("loyalty", 10)))
    state.foreign[power]["marriage"] = char.id
    adjust_relation(state, power, state.player.id, float(spec.get("alignment", 20)))
    name = nations(state)[power]["name"]
    _history(state, f"{char.name} married into {name}: an alliance of blood.")
    return [f"{char.name.upper()} MARRIED INTO {name.upper()}", f"TREASURY −{dowry:,} {state.currency} (DOWRY)",
            f"{name.upper()} ALIGNMENT +{spec.get('alignment', 20)} (NEVER BELOW +{spec.get('alignment_floor', 20)})",
            f"LEND-LEASE FROM {name.upper()} {spec.get('lend_lease_discount', 0.25):.0%} CHEAPER"]


def marriage_floor(state: GameState, power: str) -> float | None:
    if state.foreign.get(power, {}).get("marriage"):
        return float(cfg(state).get("marriage", {}).get("alignment_floor", 20))
    return None


def lend_lease_price(state: GameState, power: str, cost: int, buyer: str | None = None) -> int:
    if (buyer is None or state.is_friendly(buyer)) and state.foreign.get(power, {}).get("marriage"):
        return round(cost * (1 - float(cfg(state).get("marriage", {}).get("lend_lease_discount", 0.25))))
    return cost


# --- death and succession ----------------------------------------------------------------------------


def _history(state: GameState, text: str) -> None:
    house = dynasty(state)
    if house is not None:
        house.history.append({"turn": state.clock.turn, "text": text})
        del house.history[:-40]


def _court_email(state: GameState, subject: str, lines: list[str], report: TickReport | None,
                 classification: str = "TOP SECRET") -> None:
    email = Email(id="court_dispatch", sender="Office of the Lord Chamberlain", subject=subject,
                  classification=classification, body="\n".join(lines + ["", "— The Lord Chamberlain, Aldmark"]))
    delivered = deliver(state, email)
    if report is not None:
        report.new_messages.append(delivered)


def die(state: GameState, char: Character, cause: str, report: TickReport | None = None) -> list[str]:
    """A courtier dies. Returns change lines. The ruler's death triggers the succession."""
    from src.engine.command import next_commander, roll_traits

    if not char.alive:
        return []
    house = dynasty(state)
    stab = cfg(state).get("stability", {})
    char.alive = False
    char.died = f"WK {state.clock.turn:03d}: {cause}"
    char.office = None
    changes = [f"{display_name(char).upper()} IS DEAD ({cause.upper()})"]
    lines = [f"{display_name(char)}, {char.role}, is dead: {cause}. Aged {char.age}."]
    if char.unit_id:
        unit = state.unit(char.unit_id)
        if unit is not None:
            unit.commander = next_commander(state, unit.nation_id)
            unit.traits = roll_traits(state)
            unit.traits_known = False
            lines.append(f"{unit.commander} assumes command of the {unit.name}.")
        char.unit_id = None
    if cause == "killed in action" and char.dynasty:
        house.stability = max(0.0, house.stability + float(stab.get("royal_killed", -20)))
        state.player.adjust_morale(float(stab.get("royal_killed_morale", -4)))
        changes.append(f"DYNASTIC STABILITY {stab.get('royal_killed', -20):+g}; CIVIL MORALE "
                       f"{stab.get('royal_killed_morale', -4):+g}")
        lines.append("A prince of the blood has fallen at the front. The House is shaken; the nation mourns.")
    _history(state, f"{display_name(char)} died: {cause}.")
    was_heir = char.relation == HEIR
    if char.relation == RULER:
        changes += succeed(state, report)
    elif was_heir:
        new = _choose_heir(state)
        if new is not None:
            lines.append(f"{new.name} is now Heir to the Lord Protector.")
            changes.append(f"NEW HEIR: {new.name.upper()}")
        else:
            lines.append("There is no Heir. The succession hangs by a thread.")
            changes.append("THE HOUSE HAS NO HEIR")
    if not state.flags.get("dynastic_collapse"):
        _court_email(state, f"THE HOUSE IN MOURNING: {char.name.upper()}", lines, report)
    if report is not None:
        report.log.append(f"COURT: {display_name(char)} is dead ({cause}).")
    check_extinction(state)
    return changes


def line_of_succession(state: GameState) -> list[Character]:
    house = dynasty(state)
    if house is None:
        return []
    living = [c for c in house.living() if c.dynasty and c.relation != RULER and not c.imprisoned]
    order = {HEIR: 0, SIBLING: 1, COUSIN: 2, UNCLE_AUNT: 3}

    def key(c: Character):
        rank = order.get(c.relation, 4)
        return (rank, -c.age if c.relation == SIBLING else -c.influence, c.id)

    return sorted(living, key=key)


def _choose_heir(state: GameState) -> Character | None:
    line = line_of_succession(state)
    if not line:
        return None
    heir = line[0]
    heir.relation = HEIR
    return heir


def succeed(state: GameState, report: TickReport | None = None) -> list[str]:
    house = dynasty(state)
    stab = cfg(state).get("stability", {})
    line = line_of_succession(state)
    if not line:
        state.flags["dynastic_collapse"] = "extinct"
        return ["NO MEMBER OF THE BLOOD REMAINS"]
    old = house.ruler
    new = line[0]
    if new.unit_id:
        recall_commander(state, new.unit_id)
    new.relation, new.office, new.married_to = RULER, None, new.married_to
    new.role = "Lord Protector of the Commonwealth"
    new.loyalty = 100.0
    house.ruler_id = new.id
    for c in house.living():
        if c.relation == SPOUSE:
            c.relation, c.role = DOWAGER, "Dowager " + c.role
    heir = _choose_heir(state)
    house.stability = max(0.0, house.stability + float(stab.get("succession", -20)))
    state.player.adjust_morale(float(stab.get("succession_morale", -3)))
    _history(state, f"{new.name} succeeds {old.name} as Lord Protector.")
    lines = [f"{old.name} is dead. {new.name} has taken the oath as Lord Protector of the Commonwealth.",
             f"The new Lord Protector's disposition: {trait_names(state, new)}.",
             f"Heir: {heir.name if heir else 'NONE — the House hangs by a thread'}.",
             f"Dynastic stability {stab.get('succession', -20):+g}; civil morale {stab.get('succession_morale', -3):+g}."]
    _court_email(state, f"THE LORD PROTECTOR IS DEAD. LONG LIVE THE LORD PROTECTOR {new.name.upper()}.", lines, report)
    if report is not None:
        report.log.append(f"SUCCESSION: {new.name} is Lord Protector.")
    return [f"SUCCESSION: {new.name.upper()} IS LORD PROTECTOR"]


def check_extinction(state: GameState) -> bool:
    house = dynasty(state)
    if house is not None and not house.blood():
        state.flags["dynastic_collapse"] = "extinct"
        return True
    return False


# --- effects from event cards ------------------------------------------------------------------------


def apply_court_effect(state: GameState, value: dict) -> list[str]:
    """The `court` effect key (effects.py). Sub-keys: loyalty {id: delta}, all_loyalty, stability, execute,
    imprison, pardon, kill {character, cause}, command <id>, marry {character, power}."""
    house = dynasty(state)
    if house is None:
        return []
    changes: list[str] = []
    for char_id, delta in value.get("loyalty", {}).items():
        char = house.characters.get(char_id)
        if char and char.alive and delta:
            adjust_loyalty(char, float(delta))
            changes.append(f"{char.name.upper()} LOYALTY {'+' if delta > 0 else '−'}{abs(delta):g}")
    if value.get("all_loyalty"):
        for char in house.living():
            if char.relation != RULER:
                adjust_loyalty(char, float(value["all_loyalty"]))
        changes.append(f"COURT LOYALTY {value['all_loyalty']:+g}")
    if value.get("stability"):
        house.stability = max(0.0, min(100.0, house.stability + float(value["stability"])))
        changes.append(f"DYNASTIC STABILITY {value['stability']:+g}")
    for key, handler in (("execute", execute), ("imprison", imprison), ("pardon", pardon)):
        if value.get(key) in house.characters:
            changes += handler(state, house.characters[value[key]])
    if value.get("kill"):
        spec = value["kill"] if isinstance(value["kill"], dict) else {"character": value["kill"]}
        char = house.characters.get(spec["character"])
        if char is not None:
            changes += die(state, char, spec.get("cause", "died"))
    if value.get("command") in house.characters:
        char = house.characters[value["command"]]
        unit = frontline_unit(state)
        if char.alive and not char.unit_id and unit is not None:
            try:
                appoint_commander(state, char.id, unit.id)
                changes.append(f"{char.name.upper()} COMMANDS THE {unit.name.upper()} ({unit.designation})")
            except CourtError as error:
                changes.append(str(error).upper())
    if value.get("marry"):
        changes += marry(state, value["marry"]["character"], value["marry"]["power"])
    return changes


def preview_court_effect(state: GameState, value: dict) -> list[str]:
    house = dynasty(state)
    if house is None:
        return []
    lines = []
    for char_id, delta in value.get("loyalty", {}).items():
        char = house.characters.get(char_id)
        if char and delta:
            lines.append(f"{char.name.upper()} LOYALTY {'+' if delta > 0 else '−'}{abs(delta):g}")
    if value.get("stability"):
        lines.append(f"DYNASTIC STABILITY {value['stability']:+g}")
    for key, verb in (("execute", "EXECUTED"), ("imprison", "IMPRISONED"), ("pardon", "PARDONED")):
        if value.get(key) in house.characters:
            lines.append(f"{house.characters[value[key]].name.upper()} {verb}")
    if value.get("command") in house.characters:
        lines.append(f"{house.characters[value['command']].name.upper()} TAKES A FRONTLINE COMMAND")
    if value.get("marry"):
        from src.engine.diplomacy import nations

        spec = cfg(state).get("marriage", {})
        name = nations(state)[value["marry"]["power"]]["name"].upper()
        lines += [f"DOWRY {spec.get('dowry', 50000):,} {state.currency}",
                  f"{name}: ALIGNMENT +{spec.get('alignment', 20)}, LEND-LEASE −{spec.get('lend_lease_discount', 0.25):.0%}"]
    return lines


def execute(state: GameState, char: Character) -> list[str]:
    stab = cfg(state).get("stability", {})
    house = dynasty(state)
    changes = die(state, char, "executed for treason")
    for other in house.living():
        if other.relation != RULER:
            adjust_loyalty(other, float(stab.get("execution_fear_loyalty", -3)))
    changes.append(f"COURT LOYALTY {stab.get('execution_fear_loyalty', -3):+g} (FEAR)")
    if char.dynasty:
        house.stability = max(0.0, house.stability + float(stab.get("executed_blood", -8)))
        changes.append(f"DYNASTIC STABILITY {stab.get('executed_blood', -8):+g} (THE BLOOD OF THE HOUSE)")
    return changes


def imprison(state: GameState, char: Character) -> list[str]:
    char.imprisoned = True
    char.office = None
    if char.unit_id:
        recall_commander(state, char.unit_id)
    if char.relation == HEIR:
        char.relation = SIBLING if char.dynasty else char.relation
        _choose_heir(state)
    _history(state, f"{char.name} imprisoned in the Aldmark citadel.")
    return [f"{char.name.upper()} IMPRISONED IN THE CITADEL"]


def pardon(state: GameState, char: Character) -> list[str]:
    gain = float(cfg(state).get("treason", {}).get("pardon_loyalty", 30))
    adjust_loyalty(char, gain)
    _history(state, f"{char.name} pardoned by the Lord Protector.")
    return [f"{char.name.upper()} PARDONED: LOYALTY +{gain:g}"]


# --- audiences --------------------------------------------------------------------------------------


def _petitioners(state: GameState) -> list[Character]:
    house = dynasty(state)
    return [c for c in house.living() if c.relation != RULER and not c.imprisoned and not c.married_to]


def _petitions_for(state: GameState, char: Character) -> list[dict]:
    tags = set(char.traits)
    for t in char.traits:
        tags.update(trait_info(state, t).get("petitions", []))
    table = cfg(state).get("petitions", [])
    own = [p for p in table if tags & set(p.get("traits", []))]
    return own or [p for p in table if "any" in p.get("traits", [])]


def _fill_value(value, variables: dict):
    if isinstance(value, str):
        return fill(value, variables)
    if isinstance(value, dict):
        return {k: _fill_value(v, variables) for k, v in value.items()}
    return value


def audience_card(state: GameState, char: Character, rng=None) -> dict | None:
    rng = rng or state.rng
    petitions = _petitions_for(state, char)
    no_hospital = sorted(c.name for c in state.cities.values() if "hospital" not in c.buildings)
    if not no_hospital:
        petitions = [p for p in petitions if p["id"] != "hospital_patron"]
    if not petitions:
        return None
    petition = rng.choice(petitions)
    kestrian = sorted(r.name for r in state.world_map.regions.values() if r.owner == state.player.id)
    variables = state.text_vars() | {"name": char.name, "role": char.role,
                                     "city": rng.choice(no_hospital) if no_hospital else state.player.capital,
                                     "region": rng.choice(kestrian) if kestrian else "the provinces"}
    grant = petition["grant"]
    effects = _fill_value(dict(grant.get("effects", {})), variables)
    effects["court"] = {"loyalty": {char.id: float(grant.get("loyalty", 10))}}
    text = fill(petition["text"], variables)
    text += (f"\n\nPETITIONER: {char.name}, {char.role} — {char.relation.upper()}. LOYALTY {char.loyalty:.0f}. "
             f"TRAITS: {trait_names(state, char).upper()}.")
    return {"id": f"audience_{state.clock.turn:03d}_{char.id}", "title": fill(petition["title"], variables),
            "category": "ROYAL COURT", "text": text, "audience": char.id,
            "choices": [
                {"id": "grant", "label": grant["label"], "hint": grant.get("hint", ""), "effects": effects},
                {"id": "deny", "label": "Deny the petition", "hint": "The Lord Protector's word is final.",
                 "effects": {"court": {"loyalty": {char.id: float(petition.get("deny_loyalty", -10))}}}},
                {"id": "ignore", "label": "Keep them waiting", "hint": "Let the petitioner cool their heels.",
                 "effects": {"court": {"loyalty": {char.id: float(petition.get("ignore_loyalty", -5))}}}},
            ]}


def _raise(state: GameState, card: dict) -> None:
    from src.engine.crisis_engine import raise_emergency

    raise_emergency(state, card)


def summon_audience(state: GameState, char: Character | None = None, report: TickReport | None = None) -> dict | None:
    """A courtier (weighted toward the disgruntled) is granted an audience: raise the card."""
    candidates = _petitioners(state)
    if char is None:
        if not candidates:
            return None
        char = state.rng.choices(candidates, weights=[105 - c.loyalty for c in candidates], k=1)[0]
    card = audience_card(state, char)
    if card is None:
        return None
    _raise(state, card)
    if report is not None:
        report.dilemma = report.dilemma or card["id"]
        report.log.append(f"ROYAL COURT: {char.name} demands an audience.")
    return card


def hold_court(state: GameState) -> dict:
    """The Lord Protector opens the doors of the Audience Chamber (Royal Court screen, A)."""
    house = _require(state)
    cooldown = int(cfg(state).get("audiences", {}).get("hold_court_cooldown", 2))
    if state.clock.turn - house.last_court_turn < cooldown:
        raise CourtError(f"The Lord Protector held court in week {house.last_court_turn:03d}; "
                         f"the next audience day is week {house.last_court_turn + cooldown:03d}.")
    if state.pending_dilemma:
        raise CourtError("A decision is already waiting on the Lord Protector's desk.")
    card = summon_audience(state)
    if card is None:
        raise CourtError("Nobody at court seeks an audience.")
    house.last_court_turn = state.clock.turn
    return card


def marriage_card(state: GameState, char_id: str) -> dict:
    """The modal for a political marriage: one option per willing power, plus 'not yet'."""
    from src.engine.diplomacy import nations

    _require(state)
    char = character(state, char_id)
    if char not in marriage_candidates(state):
        raise CourtError(f"{char.name} cannot be married abroad (of the blood, unmarried, aged "
                         f"{cfg(state).get('marriage', {}).get('min_age', 16)}-"
                         f"{cfg(state).get('marriage', {}).get('max_age', 45)}, not the ruler).")
    partners = marriage_partners(state)
    if not partners:
        raise CourtError("No foreign power will take a Kestrian match now (each takes one; alignment must be ≥ 0).")
    if state.pending_dilemma:
        raise CourtError("A decision is already waiting on the Lord Protector's desk.")
    spec = cfg(state).get("marriage", {})
    choices = [{"id": f"marry_{p}", "label": f"Marry into {nations(state)[p]['name']}",
                "hint": nations(state)[p].get("leader", ""),
                "effects": {"court": {"marry": {"character": char.id, "power": p}}}} for p in partners]
    choices.append({"id": "not_yet", "label": "Not yet", "hint": "The match can wait.", "effects": {}})
    card = {"id": f"marriage_{state.clock.turn:03d}_{char.id}", "title": f"A Match for {char.name}",
            "category": "ROYAL COURT",
            "text": (f"The Lord Chamberlain has sounded out the foreign courts. {char.name}, {char.role}, aged "
                     f"{char.age}, could be married into the ruling family of a neutral power. The dowry is "
                     f"{spec.get('dowry', 50000):,} {state.currency}. The match binds that power to Kestria: its "
                     f"alignment rises by {spec.get('alignment', 20)} and never falls below +"
                     f"{spec.get('alignment_floor', 20)}, and its lend-lease comes "
                     f"{spec.get('lend_lease_discount', 0.25):.0%} cheaper. {char.name} will live abroad."),
            "choices": choices}
    _raise(state, card)
    return card


# --- treason ----------------------------------------------------------------------------------------


def _plot_type(state: GameState, char: Character) -> str | None:
    spec = cfg(state).get("treason", {}).get("plots", {})
    options = {}
    for kind, p in spec.items():
        weight = float(p.get("weight", 1))
        if kind == "embezzle" and char.has("corrupt"):
            weight = float(p.get("corrupt_weight", weight))
        if kind == "leak" and char.intrigue < int(p.get("min_intrigue", 4)):
            continue
        if kind == "assassinate" and not (trait_sum(state, char, "plots_coup") or
                                          char.loyalty < float(p.get("loyalty_below", 10))):
            continue
        if kind == "coup" and not (char.dynasty and trait_sum(state, char, "plots_coup")
                                   and char.influence >= float(p.get("min_influence", 60))):
            continue
        options[kind] = weight
    if not options:
        return None
    kinds = sorted(options)
    return state.rng.choices(kinds, weights=[options[k] for k in kinds], k=1)[0]


def treason_card(state: GameState, char: Character, kind: str) -> dict:
    what = {"embezzle": "siphoning money from the Treasury into accounts in the Iren Free Port",
            "leak": "passing our war plans to a Vosk courier through the Vaelish embassy",
            "assassinate": "hiring gunmen to kill the Lord Protector on the Cathedral steps",
            "coup": "sounding out the Guards regiments to seize the palace"}[kind]
    return {"id": f"treason_{state.clock.turn:03d}_{char.id}", "title": f"Treason: {char.name}",
            "category": "INTERNAL SECURITY", "emergency": True,
            "text": (f"The Head of Intelligence reports that {char.name}, {char.role}, has been caught {what}. "
                     f"The evidence is in the Lord Protector's hands. LOYALTY {char.loyalty:.0f}. "
                     f"TRAITS: {trait_names(state, char).upper()}."),
            "choices": [
                {"id": "execute", "label": "Execute the traitor", "hint": "The court will learn fear.",
                 "effects": {"court": {"execute": char.id}}},
                {"id": "imprison", "label": "Imprison them in the citadel", "hint": "Out of the game, alive.",
                 "effects": {"court": {"imprison": char.id}}},
                {"id": "pardon", "label": "Pardon them", "hint": "Mercy may buy loyalty. Or not.",
                 "effects": {"court": {"pardon": char.id}}},
            ]}


def run_plot(state: GameState, char: Character, kind: str, report: TickReport) -> None:
    spec = cfg(state).get("treason", {}).get("plots", {}).get(kind, {})
    house = dynasty(state)
    nation = state.player
    if kind == "embezzle":
        lo, hi = spec.get("amount", [10000, 30000])
        amount = state.rng.randint(int(lo), int(hi)) * (int(spec.get("office_multiplier", 2)) if char.office else 1)
        nation.adjust_treasury(-amount)
        _court_email(state, "TREASURY AUDIT: FUNDS MISSING", [
            f"The Treasury auditors cannot account for {amount:,} {state.currency} this week.",
            "The trail leads into the palace before it goes cold. Somebody at court is stealing."], report, "SECRET")
        report.log.append(f"COURT: {amount:,} {state.currency} embezzled.")
    elif kind == "leak":
        for unit in nation.units:
            unit.morale = max(0.0, unit.morale + float(spec.get("army_morale", -3)))
        nation.timed_modifiers.append({"key": "recon_accuracy", "value": float(spec.get("recon_accuracy", -0.15)),
                                       "weeks_left": int(spec.get("weeks", 4)), "source": "court_leak"})
        _court_email(state, "SECURITY BREACH: OUR PLANS ARE IN KARZAN", [
            "The Vosk knew where our divisions would be before our divisions did. Somebody close to the Lord "
            "Protector is talking to Karzan.",
            f"Army morale {spec.get('army_morale', -3):+g}; our intelligence picture is compromised for "
            f"{spec.get('weeks', 4)} weeks."], report)
        report.log.append("COURT: war plans leaked to the Vosk.")
    elif kind == "assassinate":
        success = float(spec.get("base_success", 0.35)) - float(spec.get("per_intrigue_point", 0.03)) * \
            office_delta(state, "intelligence")
        if state.rng.random() < max(0.05, success):
            ruler = house.ruler
            _history(state, f"{ruler.name} assassinated; the hand behind it was never proven.")
            die(state, ruler, "assassinated", report)
        else:
            _court_email(state, "ATTEMPT ON THE LORD PROTECTOR'S LIFE", [
                "Shots were fired at the Lord Protector's car on the Cathedral steps. The Lord Protector is unhurt.",
                f"The gunmen talked: they were paid by {char.name}."], report)
            _raise(state, treason_card(state, char, kind))
            report.log.append(f"COURT: assassination attempt by {char.name} failed.")
    elif kind == "coup":
        stab = cfg(state).get("stability", {})
        chance = float(spec.get("base_success", 0.25)) + (char.influence - 60) / 100 + \
            (50 - house.stability) / 200 - (nation.military_morale - 50) / 200
        if state.rng.random() < max(0.05, min(0.9, chance)):
            state.flags["dynastic_collapse"] = "coup"
            _court_email(state, f"COUP D'ÉTAT: {char.name.upper()} SEIZES THE PALACE", [
                f"At dawn the Guards regiments loyal to {char.name} occupied the palace and the radio station. "
                f"{char.name} has proclaimed a Council of the House and declared the Lord Protector deposed."], report)
            report.log.append(f"COURT: COUP BY {char.name.upper()}.")
        else:
            house.stability = max(0.0, house.stability + float(stab.get("failed_coup", -10)))
            _court_email(state, f"COUP FAILED: {char.name.upper()}", [
                f"{char.name} tried to turn the Guards against the Lord Protector. The Guards held.",
                f"Dynastic stability {stab.get('failed_coup', -10):+g}."], report)
            _raise(state, treason_card(state, char, kind))
            report.log.append(f"COURT: coup by {char.name} failed.")
    _history(state, f"Plot ({kind}).")


# --- the weekly round --------------------------------------------------------------------------------


def _weekly_health(state: GameState, report: TickReport) -> None:
    house = dynasty(state)
    health = cfg(state).get("health", {})
    for char in list(house.living()):
        if state.clock.turn % 52 == 1 and state.clock.turn > 1:
            char.age += 1
        age_p = float(health.get("age_base", 0.0015)) * 2 ** (
            (char.age - float(health.get("age_pivot", 60))) / float(health.get("age_doubling_years", 6)))
        if state.rng.random() < age_p:
            die(state, char, "died of old age", report)
            continue
        if char.ill_weeks:
            char.ill_weeks -= 1
            if state.rng.random() < float(health.get("ill_death_chance", 0.08)):
                die(state, char, "died of the fever", report)
                continue
        elif char.at_court or char.relation == RULER:
            if f"city:{health.get('plague_city', 'Aldmark')}" in state.infections and \
                    state.rng.random() < float(health.get("plague_chance", 0.04)):
                char.ill_weeks = int(health.get("ill_weeks", 4))
                report.log.append(f"COURT: {display_name(char)} has fallen ill.")
                _history(state, f"{display_name(char)} fell ill with the fever.")
        if char.unit_id:
            unit = state.unit(char.unit_id)
            if unit is None:
                die(state, char, "killed in action", report)
                continue
            if unit.commander != char.name:  # relieved from the Military screen
                char.unit_id = None
                continue
            if unit.status == ENGAGED:
                chance = float(health.get("front_death_chance", 0.03)) * max(
                    1.0, trait_sum(state, char, "front_death_multiplier") or 1.0)
                if state.rng.random() < chance:
                    die(state, char, "killed in action", report)


def _weekly_loyalty(state: GameState, report: TickReport) -> None:
    house = dynasty(state)
    data = cfg(state)
    stab = data.get("stability", {})
    nation = state.player
    drift = float(data.get("loyalty", {}).get("drift", 0.05))
    for char in house.living():
        if char.relation == RULER or char.imprisoned:
            continue
        char.loyalty = round(char.loyalty + (loyalty_target(state, char) - char.loyalty) * drift, 2)
    # stability
    target = float(stab.get("recovery_target", 60))
    step = float(stab.get("recovery_per_week", 0.5))
    house.stability += max(-step, min(step, target - house.stability))
    if nation.morale < float(stab.get("low_morale_below", 30)):
        house.stability += float(stab.get("low_morale_per_week", -0.5))
    if house.stability < float(stab.get("unrest_below", 25)):
        nation.adjust_morale(float(stab.get("unrest_civil_morale", -0.25)))
    # traits felt across the nation, and the Minister of War
    civil = military = 0.0
    for char in house.living():
        present = char.relation == RULER or char.at_court
        if present:
            civil += trait_sum(state, char, "civil_morale_per_week")
            military += trait_sum(state, char, "military_morale_per_week")
            house.stability += trait_sum(state, char, "stability_per_week")
        if char.office:
            civil += trait_sum(state, char, "office_civil_morale_per_week")
            military += trait_sum(state, char, "office_military_morale_per_week")
    per = float(data.get("cabinet", {}).get("war", {}).get("military_morale_per_point", 0.05))
    military += per * office_delta(state, "war")
    if civil:
        nation.adjust_morale(civil)
    if military:
        nation.adjust_military_morale(military)
    house.stability = max(0.0, min(100.0, house.stability))
    # paranoia: false arrests among the staff
    for char in house.living():
        chance = trait_sum(state, char, "false_arrest_chance")
        if chance and (char.at_court or char.office) and state.rng.random() < chance:
            victims = [c for c in _petitioners(state) if c.id != char.id]
            if victims:
                victim = state.rng.choice(victims)
                adjust_loyalty(victim, -5)
                report.log.append(f"COURT: {char.name}'s agents arrested {victim.name}'s secretary on a false charge.")
                _history(state, f"{char.name} had {victim.name}'s household searched (loyalty −5).")


def _weekly_treason(state: GameState, report: TickReport) -> None:
    spec = cfg(state).get("treason", {})
    threshold = float(spec.get("threshold", 20))
    uncovered = False
    for char in list(dynasty(state).living()):
        if state.flags.get("dynastic_collapse") or not dynasty(state).ruler.alive:
            return
        if char.relation == RULER or char.imprisoned or char.loyalty >= threshold:
            continue
        if state.rng.random() >= float(spec.get("plot_chance", 0.2)):
            continue
        kind = _plot_type(state, char)
        if kind is None:
            continue
        if not uncovered and state.rng.random() < detection_chance(state, char):
            uncovered = True
            _raise(state, treason_card(state, char, kind))
            report.dilemma = report.dilemma or f"treason_{state.clock.turn:03d}_{char.id}"
            report.log.append(f"INTERNAL SECURITY: plot uncovered — {char.name}.")
            continue
        run_plot(state, char, kind, report)


class CourtSystem(SimulationSystem):
    """The court: aging, illness, deaths at the front, succession, loyalty, the cabinet, treason, audiences."""

    name = "court"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        house = dynasty(state)
        if house is None or state.game_over:
            return
        _weekly_health(state, report)
        if state.flags.get("dynastic_collapse"):
            return
        _weekly_loyalty(state, report)
        _weekly_treason(state, report)
        if state.flags.get("dynastic_collapse"):
            return
        spec = cfg(state).get("audiences", {})
        if state.clock.turn >= house.next_audience_turn:
            summon_audience(state, report=report)
            house.next_audience_turn = state.clock.turn + state.rng.randint(int(spec.get("min_weeks", 3)),
                                                                            int(spec.get("max_weeks", 5)))
