"""Foreign Affairs & Lend-Lease: the off-map world (data/diplomacy.json, Diplomacy tab `7`).

Three powers watch the war (the design team's faction sheet): the Oakhaven Republic (a naval superpower
across the western ocean that despises oppression), the United Provinces of Tor (an industrial autocracy to
the south that respects strength) and the Sovereign State of Vael (a neutral scientific hub that sells no
weapons of war). Each has ONE ALIGNMENT score, -100 (with the Vosk) .. +100 (with Kestria),
`state.foreign[power]["alignment"]`; `relation(state, power, belligerent)` reads it from that side.

  * ENVOYS: a gift (gift_cost CR) swings alignment toward the sender, less the further it already leans.
    Alignment drifts back toward the starting value every week; ideology bounds it (floor / ceiling).
  * PREFERENCES: Oakhaven cools every week Kestria taxes at High or Oppressive rates; Tor swings three times
    as hard as anyone toward whoever wins battles; Vael does not care who wins.
  * TRADE AGREEMENTS add weekly income while one of the partner's ports is open; with every port blockaded
    the trade is suspended. A power cancels when it drifts back to the other side.
  * LEND-LEASE: pay CR now; the convoy sails for delivery_weeks to the first open port and unloads into the
    national stockpile. While every port is blockaded it waits at sea; each week at sea, every enemy
    submarine wolfpack out of port may torpedo part of the cargo — and the neutral blames the attacker.
  * The VOSK foreign ministry does all of this too, from the other end of the scale; Kestrian blockades of
    Vosk ports hold its convoys at sea.
"""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.systems import SimulationSystem, TickReport
from src.models import Email, GameState


class DiplomacyError(ValueError):
    """An invalid diplomatic action."""


def _cfg(state: GameState) -> dict:
    return state.catalog.get("diplomacy", {})


def nations(state: GameState) -> dict[str, dict]:
    """Power id -> faction data (from `factions`, keyed by the faction's `id`)."""
    return {f["id"]: f for f in _cfg(state).get("factions", {}).values()}


def packages(state: GameState, nation_id: str) -> list[dict]:
    """The power's lend-lease offers, normalised: {id, name, items, cost, min_alignment, weeks}."""
    items = {e["id"]: e for e in state.catalog.get("equipment", [])}
    out = []
    for p in nations(state)[nation_id].get("lend_lease_inventory", []):
        contents = dict(p.get("items") or {p["item"]: p["quantity"]})
        first = next(iter(contents))
        name = p.get("name") or (f"{items.get(first, {}).get('name', first)} ×{contents[first]:,}")
        out.append({"id": p["id"], "name": name, "items": contents, "cost": int(p["cost_cr"]),
                    "min_alignment": float(p["min_alignment"]), "weeks": int(p["delivery_weeks"])})
    return out


def foreign(state: GameState, nation_id: str) -> dict:
    """Mutable state of a power: {alignment, trade: [belligerents with a treaty]}."""
    return state.foreign[nation_id]


def init_foreign(state: GameState) -> None:
    for nid, data in nations(state).items():
        state.foreign[nid] = {"alignment": float(data.get("starting_alignment", 0)),
                              "trade": list(data.get("trade_signed", []))}


def _sign(state: GameState, belligerent: str) -> float:
    return 1.0 if belligerent == state.player.id else -1.0


def relation(state: GameState, nation_id: str, belligerent: str | None = None) -> float:
    """How far the power leans toward `belligerent` (+100 = firmly with them)."""
    return foreign(state, nation_id)["alignment"] * _sign(state, belligerent or state.player.id)


def rival_of(state: GameState, belligerent: str) -> str:
    return next(n for n in state.nations if n != belligerent)


def bounds(state: GameState, nation_id: str) -> tuple[float, float]:
    data = nations(state)[nation_id]
    low, high = float(data.get("alignment_floor", -100)), float(data.get("alignment_ceiling", 100))
    from src.engine.court import marriage_floor

    floor = marriage_floor(state, nation_id)  # a royal marriage binds the power to Kestria
    return (low if floor is None else max(low, floor)), high


def adjust_relation(state: GameState, nation_id: str, belligerent: str, delta: float) -> float:
    """Swing the power `delta` toward (or away from) `belligerent`. Returns its new relation with them."""
    low, high = bounds(state, nation_id)
    entry = foreign(state, nation_id)
    current = entry["alignment"]
    new = current + delta * _sign(state, belligerent)
    if new > current:
        new = min(new, max(high, current))  # ideology: gifts cannot buy what the regime forbids
    elif new < current:
        new = max(new, min(low, current))
    entry["alignment"] = max(-100.0, min(100.0, new))
    return relation(state, nation_id, belligerent)


def standing(score: float) -> str:
    for upper, label in ((-50, "HOSTILE"), (-15, "COLD"), (15, "NEUTRAL"), (45, "FRIENDLY"), (101, "ALLIED")):
        if score < upper:
            return label
    return "ALLIED"


def leaning(state: GameState, nation_id: str) -> str:
    """How the power leans, in words, from Kestria's point of view."""
    a = foreign(state, nation_id)["alignment"]
    if a >= 45:
        return "FIRMLY WITH KESTRIA"
    if a >= 15:
        return "LEANS KESTRIA"
    if a > -15:
        return "NEUTRAL"
    if a > -45:
        return "LEANS VOSK"
    return "FIRMLY WITH THE VOSK"


# --- ports -------------------------------------------------------------------------------------


def open_port(state: GameState, belligerent: str) -> str | None:
    """The first of the belligerent's trade ports that is not under blockade."""
    world = state.world_map
    for name in _cfg(state).get("ports", {}).get(belligerent, []):
        port = world.feature_named(name)
        if port is not None and world.owner_at(port.x, port.y) == belligerent and name not in state.blockades:
            return name
    return None


# --- actions ------------------------------------------------------------------------------------


def send_envoy(state: GameState, nation_id: str, belligerent: str | None = None) -> float:
    """A diplomatic gift. Returns how far the power moved toward the sender."""
    belligerent = belligerent or state.player.id
    if state.game_over:
        raise DiplomacyError("The government has fallen. The terminal is locked.")
    cfg = _cfg(state)
    payer = state.nations[belligerent]
    from src.engine.court import regency_price

    cost = regency_price(state, payer, int(cfg.get("gift_cost", 25000)))
    if payer.treasury < cost:
        raise DiplomacyError(f"The envoy needs {cost:,} {state.currency} in gifts; the treasury cannot spare it.")
    payer.adjust_treasury(-cost)
    current = relation(state, nation_id, belligerent)
    gain = max(1.0, float(cfg.get("gift_gain", 8)) * (100 - current) / 100)
    return adjust_relation(state, nation_id, belligerent, gain) - current


def sign_trade(state: GameState, nation_id: str, belligerent: str | None = None) -> None:
    belligerent = belligerent or state.player.id
    data = nations(state)[nation_id]
    treaties = foreign(state, nation_id)["trade"]
    if belligerent in treaties:
        raise DiplomacyError("A trade agreement is already in force.")
    need = float(data.get("trade_min_alignment", 10))
    if relation(state, nation_id, belligerent) < need:
        side = "toward us" if state.is_friendly(belligerent) else "toward them"
        raise DiplomacyError(f"{data['name']} will not sign until it leans {need:+.0f} {side}.")
    treaties.append(belligerent)


def cancel_trade(state: GameState, nation_id: str, belligerent: str | None = None) -> None:
    belligerent = belligerent or state.player.id
    treaties = foreign(state, nation_id)["trade"]
    if belligerent not in treaties:
        raise DiplomacyError("There is no trade agreement to cancel.")
    treaties.remove(belligerent)


def trade_income(state: GameState, belligerent: str) -> tuple[int, list[str], list[str]]:
    """(weekly income, partners trading, partners suspended by blockade) for a belligerent."""
    port = open_port(state, belligerent)
    income, active, suspended = 0, [], []
    for nid, data in nations(state).items():
        if belligerent not in foreign(state, nid)["trade"]:
            continue
        if port is None:
            suspended.append(nid)
            continue
        income += int(data.get("trade_income_bonus_cr", 0))
        active.append(nid)
    return income, active, suspended


def package(state: GameState, nation_id: str, package_id: str) -> dict:
    found = next((p for p in packages(state, nation_id) if p["id"] == package_id), None)
    if found is None:
        raise DiplomacyError(f"Unknown lend-lease package {package_id!r}")
    return found


def buy_lend_lease(state: GameState, nation_id: str, package_id: str, belligerent: str | None = None) -> dict:
    """Pay for a package; the convoy sails. Returns the shipment."""
    belligerent = belligerent or state.player.id
    if state.game_over:
        raise DiplomacyError("The government has fallen. The terminal is locked.")
    offer = package(state, nation_id, package_id)
    name = nations(state)[nation_id]["name"]
    if relation(state, nation_id, belligerent) < offer["min_alignment"]:
        raise DiplomacyError(f"{name} will not sell the {offer['name']} until it leans {offer['min_alignment']:+.0f} "
                             f"{'toward us' if state.is_friendly(belligerent) else 'toward them'}.")
    buyer = state.nations[belligerent]
    from src.engine.court import lend_lease_price, regency_price

    # royal in-laws sell cheaper; a regency's waste costs more
    cost = regency_price(state, buyer, lend_lease_price(state, nation_id, offer["cost"], belligerent))
    if buyer.treasury < cost:
        raise DiplomacyError(f"The {offer['name']} costs {cost:,} {state.currency}.")
    buyer.adjust_treasury(-cost)
    shipment = {"id": f"LL-{state.clock.turn:03d}-{len(state.shipments) + 1:02d}", "from": nation_id,
                "to": belligerent, "package": package_id, "name": offer["name"], "items": dict(offer["items"]),
                "weeks_left": offer["weeks"], "ordered": state.clock.turn, "status": "AT SEA", "losses": 0}
    state.shipments.append(shipment)
    return shipment


# --- the weekly round -----------------------------------------------------------------------------


def _wolfpacks_at_sea(state: GameState, enemy_of: str) -> int:
    from src.engine.naval_engine import fleets, in_port

    return sum(1 for u in fleets(state) if u.nation_id != enemy_of and state.template(u.unit_type).get("stealth")
               and not u.routing and not in_port(state, u))


def run_shipments(state: GameState, report: TickReport) -> None:
    cfg = _cfg(state)
    for ship in list(state.shipments):
        buyer = state.nations[ship["to"]]
        for _ in range(_wolfpacks_at_sea(state, ship["to"])):  # U-boats hunt the convoy lanes
            if state.rng.random() < float(cfg.get("sub_attack_chance", 0.08)):
                loss = float(cfg.get("sub_loss", 0.3))
                for item, qty in ship["items"].items():
                    ship["items"][item] = int(qty * (1 - loss))
                ship["losses"] += 1
                adjust_relation(state, ship["from"], rival_of(state, ship["to"]), -5)  # neutral ships sunk
                if state.is_friendly(ship["to"]):
                    report.new_messages.append(deliver(state, convoy_email(state, ship, torpedoed=True)))
                    report.log.append(f"CONVOY {ship['id']} TORPEDOED: {loss:.0%} of the cargo lost.")
        if ship["weeks_left"] > 1:
            ship["weeks_left"] -= 1
            continue
        port = open_port(state, ship["to"])
        if port is None:
            ship["status"] = "WAITING — EVERY PORT BLOCKADED"
            continue
        for item, qty in ship["items"].items():
            buyer.national_stockpile[item] = buyer.national_stockpile.get(item, 0) + int(qty)
        state.shipments.remove(ship)
        if state.is_friendly(ship["to"]):
            ship["port"] = port
            report.new_messages.append(deliver(state, convoy_email(state, ship, torpedoed=False)))
            report.log.append(f"LEND-LEASE: {ship['name']} unloaded at {port}.")


def convoy_email(state: GameState, ship: dict, torpedoed: bool) -> Email:
    items = {e["id"]: e for e in state.catalog["equipment"]}
    source = nations(state)[ship["from"]]["name"]
    cargo = [f"  • {items.get(i, {}).get('name', i)}: {q:,}" for i, q in ship["items"].items() if q]
    if torpedoed:
        subject = f"CONVOY TORPEDOED: {ship['name']} ({ship['id']})"
        body = [f"Enemy submarines attacked the {source} convoy carrying the {ship['name']}. Freighters were lost with "
                "all hands. Surviving cargo:", *cargo, "",
                "Destroyers are the answer to the wolfpacks. The convoy sails on."]
    else:
        subject = f"LEND-LEASE DELIVERED: {ship['name']}"
        body = [f"The {source} convoy has docked at {ship.get('port', 'port')} and unloaded. Into the national stockpile:",
                *cargo, "", "The logistics pipeline will issue it to the formations that need it."]
    body += ["", "— Ministry of Supply, Overseas Procurement"]
    return Email(id="lend_lease", sender="Ministry of Supply — Overseas Procurement", subject=subject,
                 classification="SECRET", body="\n".join(body))


def drift_and_treaties(state: GameState, report: TickReport) -> None:
    step = float(_cfg(state).get("drift_per_week", 1.0))
    player = state.player
    for nid, data in nations(state).items():
        entry = foreign(state, nid)
        base = float(data.get("starting_alignment", 0))
        now = entry["alignment"]
        entry["alignment"] = base if abs(now - base) <= step else now + (step if base > now else -step)
        tax = data.get("preferences", {}).get("tax_policy", {}).get(player.tax_policy)
        if tax:  # Oakhaven watches how Kestria treats its own citizens
            adjust_relation(state, nid, player.id, float(tax))
        cancel = float(data.get("trade_cancel_alignment", 0))
        for belligerent in list(entry["trade"]):
            if relation(state, nid, belligerent) < cancel:
                entry["trade"].remove(belligerent)
                if state.is_friendly(belligerent):
                    report.log.append(f"{data['name']} has cancelled its trade agreement with us.")


def battle_standing(state: GameState, winner: str) -> None:
    """The powers lean toward whoever is winning (Tor three times as hard, Vael not at all)."""
    gain = float(_cfg(state).get("victory_alignment", 1))
    for nid, data in nations(state).items():
        weight = float(data.get("preferences", {}).get("victory_weight", 1))
        if weight:
            adjust_relation(state, nid, winner, gain * weight)


def ai_foreign_policy(state: GameState, belligerent: str) -> None:
    """The Vosk foreign ministry: woo the powers, trade, and buy arms when it can afford them."""
    cfg = _cfg(state).get("ai", {})
    nation = state.nations[belligerent]
    rng = state.rng
    rich = nation.treasury > float(cfg.get("min_treasury", 900000))
    for nid, data in sorted(nations(state).items()):
        rel = relation(state, nid, belligerent)
        low, high = bounds(state, nid)
        limit = high if state.is_friendly(belligerent) else -low
        if rich and rel < min(float(cfg.get("gift_until", 40)), limit) and rng.random() < float(cfg.get("gift_chance", 0.15)):
            try:
                send_envoy(state, nid, belligerent)
            except DiplomacyError:
                pass
        if belligerent not in foreign(state, nid)["trade"] and rel >= float(data.get("trade_min_alignment", 10)):
            sign_trade(state, nid, belligerent)
        if rich and rng.random() < float(cfg.get("buy_chance", 0.12)):
            affordable = [p for p in packages(state, nid) if rel >= p["min_alignment"]]
            if affordable:
                try:
                    buy_lend_lease(state, nid, rng.choice(affordable)["id"], belligerent)
                except DiplomacyError:
                    pass


class DiplomacySystem(SimulationSystem):
    """Alignment drifts, treaties lapse, the enemy's foreign ministry works, convoys sail and land."""

    name = "diplomacy"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        drift_and_treaties(state, report)
        for belligerent in state.ai_states:
            ai_foreign_policy(state, belligerent)
        run_shipments(state, report)
        _, _, suspended = trade_income(state, state.player.id)
        if suspended:
            report.log.append("Foreign trade SUSPENDED: every port is blockaded.")
