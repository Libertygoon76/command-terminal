"""The national economy: taxation, trade, the weekly ledger, and bankruptcy.

Weekly income:
    tax      = population x tax_rate x tax_revenue_per_capita_weekly x productivity(civil morale)
    industry = nation.trade_income x productivity(civil morale)     (domestic trade & industry)
    overseas = sum of the nation's OPEN ports' `trade` (world.json) x productivity. A port under naval
               BLOCKADE (naval_engine) earns nothing; the ledger shows what is being lost.
Weekly expenses: civil administration, armed forces pay, military production (per assigned
factory), the active research project, and interest on debt.

Tax POLICY (config economy.tax_policies: Low / Normal / High / Oppressive) sets the tax rate and a
weekly drift in civil morale: higher taxes fill the treasury and slowly sour the people.

BANKRUPTCY: whenever the treasury is at or below 0 the state cannot pay its servants or its army.
Civil and military morale fall every week (economy.bankruptcy) and research halts, until funds are
restored. After fail_states.bankruptcy_grace_weeks of it, the state collapses.

The ledger runs for every nation (the AI pays for its own factories, research and recruits);
insolvency tracking and fail states apply to the player.
"""

from __future__ import annotations

from src.engine.systems import SimulationSystem, TickReport
from src.models import GameState, Ledger, Nation


class EconomyError(ValueError):
    """An invalid economic decision."""


def productivity(morale: float) -> float:
    """Unhappy populations work less. 75% at morale 0, 100% at morale 100."""
    return 0.75 + morale / 400


def _cfg(state: GameState) -> dict:
    return state.config.get("economy", {})


def tax_policies(state: GameState) -> list[dict]:
    return _cfg(state).get("tax_policies", [])


def tax_policy(state: GameState, nation: Nation) -> dict:
    policies = {p["id"]: p for p in tax_policies(state)}
    return policies.get(nation.tax_policy, {"id": nation.tax_policy, "name": nation.tax_policy.title(),
                                             "rate": nation.tax_rate, "morale_per_week": 0.0})


def set_tax_policy(state: GameState, nation_id: str, policy_id: str) -> dict:
    policies = {p["id"]: p for p in tax_policies(state)}
    if policy_id not in policies:
        raise EconomyError(f"Unknown tax policy {policy_id!r}")
    if state.game_over:
        raise EconomyError("The government has fallen. The terminal is locked.")
    nation = state.nations[nation_id]
    nation.tax_policy = policy_id
    nation.tax_rate = float(policies[policy_id]["rate"])
    return policies[policy_id]


def shift_tax_policy(state: GameState, nation_id: str, step: int) -> dict:
    """Move one notch up (+1) or down (-1) the policy ladder."""
    ids = [p["id"] for p in tax_policies(state)]
    nation = state.nations[nation_id]
    index = ids.index(nation.tax_policy) if nation.tax_policy in ids else 1
    new = max(0, min(len(ids) - 1, index + step))
    if new == index:
        raise EconomyError("Taxes are already at the " + ("lowest" if step < 0 else "highest") + " setting.")
    return set_tax_policy(state, nation_id, ids[new])


def compute_ledger(state: GameState, nation: Nation | None = None) -> Ledger:
    cfg = _cfg(state)
    nation = nation or state.player
    ledger = Ledger(turn=state.clock.turn)

    prod = productivity(nation.morale)
    tax = nation.population * nation.tax_rate * float(cfg.get("tax_revenue_per_capita_weekly", 0.042)) * prod
    ledger.income[f"Tax revenue ({nation.tax_rate:.0%} rate, {prod:.0%} productivity)"] = round(tax)
    from src.engine.court import tax_adjustments

    for label, amount in tax_adjustments(state, nation, tax):  # the Minister of Finance, a corrupt hand
        if amount > 0:
            ledger.income[label] = amount
        elif amount < 0:
            ledger.expenses[label] = -amount
    if nation.trade_income:
        ledger.income["Trade & industry"] = round(nation.trade_income * prod)
    from src.engine.naval_engine import lost_trade, port_trade

    overseas, open_ports = port_trade(state, nation.id)
    if overseas:
        ledger.income[f"Overseas trade ({open_ports} open port{'s' if open_ports != 1 else ''})"] = round(overseas * prod)
    from src.engine.diplomacy import nations as foreign_nations
    from src.engine.diplomacy import trade_income

    if state.foreign:
        income, active, suspended = trade_income(state, nation.id)
        if income:
            ledger.income[f"Foreign trade agreements ({len(active)})"] = round(income * prod)
        if suspended:
            names = ", ".join(foreign_nations(state)[n]["name"] for n in suspended)
            ledger.notes.append(f"FOREIGN TRADE SUSPENDED (every port blockaded): {names}")
    lost = lost_trade(state, nation.id)
    if lost:
        ledger.notes.append(f"NAVAL BLOCKADE: {lost:,} {state.currency}/week of overseas trade lost")

    ledger.expenses["Civil administration"] = int(cfg.get("civil_upkeep_weekly", 38000))
    ledger.expenses["Armed forces pay"] = int(cfg.get("military_upkeep_weekly", 16000))
    factories = nation.assigned_factories
    if factories:
        upkeep = int(state.config.get("production", {}).get("upkeep_per_factory", 1100))
        ledger.expenses[f"Military production ({factories} factories)"] = factories * upkeep
    if nation.research_project:
        tech = next((t for t in state.catalog["tech_tree"]["techs"] if t["id"] == nation.research_project), None)
        if tech and tech.get("weekly_cost"):
            ledger.expenses[f"Research: {tech['name']}"] = int(tech["weekly_cost"])
    if nation.treasury < 0:
        ledger.expenses["Debt interest"] = round(-nation.treasury * float(cfg.get("debt_interest_rate_weekly", 0.01)))
    from src.engine.court import regency_surcharge

    surcharge = regency_surcharge(state, nation)
    if surcharge:  # a Regent governs: waste and open hands on every line
        ledger.expenses[f"Regency: waste and embezzlement (+{surcharge:.0%})"] = round(
            sum(ledger.expenses.values()) * surcharge)
    return ledger


def run_economy(state: GameState, nation: Nation) -> Ledger:
    cfg = _cfg(state)
    ledger = compute_ledger(state, nation)
    nation.adjust_treasury(ledger.net)
    from src.engine.effects import tick_timed_modifiers

    for mod in tick_timed_modifiers(nation):
        ledger.notes.append(f"Expired: {mod['key'].replace('_', ' ')} {mod['value']:+.0%}")

    drift = float(tax_policy(state, nation).get("morale_per_week", 0.0))
    if drift:
        nation.adjust_morale(drift)
        ledger.notes.append(f"Tax policy {tax_policy(state, nation)['name'].upper()}: civil morale "
                            f"{'+' if drift > 0 else '−'}{abs(drift):g}")

    if nation.bankrupt:
        penalties = cfg.get("bankruptcy", {})
        civil = float(penalties.get("civil_morale_per_week", 4))
        military = float(penalties.get("military_morale_per_week", 6))
        nation.adjust_morale(-civil)
        nation.adjust_military_morale(-military)
        ledger.notes.append(f"STATE BANKRUPT: salaries unpaid — civil morale −{civil:g}, military morale −{military:g}")
    return ledger


class EconomyEngine(SimulationSystem):
    name = "economy"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        for nation_id, nation in state.nations.items():
            ledger = run_economy(state, nation)
            if not state.is_friendly(nation_id):
                continue
            state.weeks_insolvent = state.weeks_insolvent + 1 if nation.bankrupt else 0
            state.last_ledger = ledger
            report.log.append(f"Net {'+' if ledger.net >= 0 else '−'}{abs(ledger.net):,} {state.currency}.")
            if nation.bankrupt:
                report.log.append("STATE BANKRUPT.")
