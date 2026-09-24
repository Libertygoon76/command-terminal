"""Economy. Phase 2 placeholder: flat tax revenue minus flat upkeep.

Phase 3 replaces this with production chains, stockpile consumption, markets and trade.
All coefficients live in data/config.json under "economy".
"""

from __future__ import annotations

from src.engine.systems import SimulationSystem, TickReport
from src.models import GameState, Ledger


def productivity(morale: float) -> float:
    """Unhappy populations work less. 75% at morale 0, 100% at morale 100."""
    return 0.75 + morale / 400


def compute_ledger(state: GameState) -> Ledger:
    cfg = state.config.get("economy", {})
    nation = state.player
    ledger = Ledger(turn=state.clock.turn)

    prod = productivity(nation.morale)
    tax = nation.population * nation.tax_rate * float(cfg.get("tax_revenue_per_capita_weekly", 0.042)) * prod
    ledger.income[f"Tax revenue ({nation.tax_rate:.0%} rate, {prod:.0%} productivity)"] = round(tax)

    ledger.expenses["Civil administration"] = int(cfg.get("civil_upkeep_weekly", 38000))
    ledger.expenses["Armed forces pay"] = int(cfg.get("military_upkeep_weekly", 16000))
    factories = nation.assigned_factories
    if factories:
        upkeep = int(state.config.get("production", {}).get("upkeep_per_factory", 1100))
        ledger.expenses[f"Military production ({factories} factories)"] = factories * upkeep
    if nation.treasury < 0:
        ledger.expenses["Debt interest"] = round(-nation.treasury * float(cfg.get("debt_interest_rate_weekly", 0.01)))
    return ledger


class EconomyEngine(SimulationSystem):
    name = "economy"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        cfg = state.config.get("economy", {})
        nation = state.player

        ledger = compute_ledger(state)
        nation.adjust_treasury(ledger.net)

        # Taxes above the tolerance threshold erode civil morale every week.
        threshold = float(cfg.get("tax_discontent_threshold", 0.25))
        if nation.tax_rate > threshold:
            penalty = (nation.tax_rate - threshold) * float(cfg.get("tax_discontent_factor", 20))
            nation.adjust_morale(-penalty)
            ledger.notes.append(f"Tax discontent: civil morale −{penalty:.1f}")

        if nation.treasury < 0:
            state.weeks_insolvent += 1
            penalty = float(cfg.get("unpaid_military_morale_penalty", 3))
            nation.adjust_military_morale(-penalty)
            ledger.notes.append(f"Pay arrears: military morale −{penalty:g}")
        else:
            state.weeks_insolvent = 0

        state.last_ledger = ledger
        report.log.append(f"Net {'+' if ledger.net >= 0 else '−'}{abs(ledger.net):,} {state.currency}.")
