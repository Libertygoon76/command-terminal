"""Generated dispatches: the weekly status & financial report."""

from __future__ import annotations

from src.engine.event_manager import deliver
from src.engine.systems import SimulationSystem, TickReport
from src.models import Email, GameState

WIDTH = 58


def _row(label: str, amount: int, currency: str, sign: bool = True) -> str:
    value = f"{'+' if amount >= 0 else '−'}{abs(amount):,} {currency}" if sign else f"{amount:,} {currency}"
    dots = max(2, WIDTH - len(label) - len(value) - 2)
    return f"  {label} {'.' * dots} {value}"


def _stat(label: str, value: str) -> str:
    return f"  {label} {'.' * max(2, 22 - len(label))} {value}"


def build_status_body(state: GameState) -> str:
    nation = state.player
    ledger = state.last_ledger
    cur = state.currency
    fail = state.config.get("fail_states", {})
    prev_date = state.clock.date_of(state.clock.turn - 1).isoformat()

    lines = [f"PERIOD: {prev_date} → {state.clock.date_str}", ""]
    if ledger:
        lines.append("INCOME")
        lines += [_row(label, amount, cur) for label, amount in ledger.income.items()]
        lines.append("EXPENDITURE")
        lines += [_row(label, -amount, cur) for label, amount in ledger.expenses.items()]
        lines.append("  " + "─" * WIDTH)
        lines.append(_row("NET CHANGE", ledger.net, cur))
    lines.append(_row("TREASURY", nation.treasury, cur, sign=False))
    lines.append("")

    lines.append("NATIONAL CONDITION")
    lines.append(_stat("Civil morale", f"{nation.morale:.0f} / 100  ({nation.morale_band})"))
    lines.append(_stat("Military morale", f"{nation.military_morale:.0f} / 100  ({nation.military_morale_band})"))
    lines.append(_stat("Manpower pool", f"{nation.manpower:,}"))
    from src.engine.economy_engine import tax_policy

    lines.append(_stat("Tax policy", f"{tax_policy(state, nation)['name'].upper()} ({nation.tax_rate:.0%})"))
    if nation.research_project:
        from src.engine.research import techs, weeks_remaining

        tech = techs(state)[nation.research_project]
        lines.append(_stat("Research", f"{tech['name']} — {weeks_remaining(state, nation, tech['id']):.0f} wk left"))
    else:
        lines.append(_stat("Research", "NO ACTIVE PROJECT"))
    from src.engine.weather_engine import describe as weather_text

    lines.append(_stat("Weather", weather_text(state)))
    world = state.world_map
    closed = [p for p in state.blockades if world.owner_at(*world.feature_named(p).location) == nation.id]
    if closed:
        lines.append(_stat("Ports BLOCKADED", ", ".join(closed)))
    if state.jammed:
        from src.engine.electronic_warfare import zone_name

        lines.append(_stat("JAMMED", "; ".join(zone_name(state, z) for _, z in sorted(state.jammed.items()))))
    from src.engine.engineering import damaged_cells

    wrecked = [c for c in damaged_cells(state) if world.owner_at(*c) == nation.id]
    if wrecked:
        lines.append(_stat("Wrecked rail/road", f"{len(wrecked)} section(s) in our territory"))
    from src.engine.crisis_engine import health_summary

    for line in health_summary(state):
        lines.append(_stat("EPIDEMIC", line))
    relief = [u.designation for u in nation.units if u.relief_weeks]
    if relief:
        lines.append(_stat("On relief duty", ", ".join(relief)))
    in_training = [o for o in state.training if o.nation_id == nation.id]
    lines.append(_stat("In training", ", ".join(f"{o.designation} ({o.weeks_left} wk)" for o in in_training) or "none"))
    awaiting = state.inbox.awaiting_response()
    expiring = [m for m in awaiting if m.reply_by_turn == state.clock.turn]
    lines.append(_stat("Awaiting your reply", f"{len(awaiting)} ({len(expiring)} due this week)"))
    lines.append("")

    units = nation.units
    lines.append("FRONT SITUATION")
    lines.append(_stat("Formations moving", str(sum(1 for u in units if u.active_order))))
    lines.append(_stat("Formations engaged", str(sum(1 for u in units if u.engaged))))
    visible = sum(1 for c in state.contacts.values() if c.visible)
    lost = sum(1 for c in state.contacts.values() if not c.visible)
    lines.append(_stat("Hostile contacts", f"{visible} observed · {lost} lost"))
    in_supply = sum(1 for u in units if u.supply_state == "supplied")
    lines.append(_stat("In supply", f"{in_supply} of {len(units)} formations"))
    active = [b for b in state.battles.values() if b.active]
    lines.append(_stat("Battles in progress", ", ".join(b.name for b in active) if active else "none"))
    lines.append("")

    lines.append("WAR INDUSTRY")
    lines.append(_stat("Military factories", f"{nation.assigned_factories} assigned · {nation.free_factories} idle"))
    items = {e["id"]: e for e in state.catalog["equipment"]}
    produced = state.last_production.get(nation.id, {}).get("produced", {})
    for item_id, made in sorted(produced.items()):
        lines.append(_stat(items[item_id]["name"][:22], f"+{made:,} → stockpile {nation.national_stockpile.get(item_id, 0):,}"))
    lines.append("")

    warnings = list(ledger.notes) if ledger else []
    coup_at = float(fail.get("coup_military_morale", 10))
    grace = int(fail.get("bankruptcy_grace_weeks", 8))
    if nation.morale < 20:
        warnings.append("Civil order is collapsing. Revolution is imminent at 0 morale.")
    if nation.military_morale < coup_at + 10:
        warnings.append(f"Officer corps disloyal. A coup is likely below {coup_at:.0f} military morale.")
    isolated = [u.designation for u in nation.units if u.supply_state == "isolated"]
    overextended = [u.designation for u in nation.units if u.supply_state == "overextended"]
    starving = [u.designation for u in nation.units if u.supply <= 0]
    if isolated:
        warnings.append(f"Supply lines cut (ISOLATED): {', '.join(isolated)}.")
    if overextended:
        warnings.append(f"Beyond supply range (OVEREXTENDED): {', '.join(overextended)}.")
    if starving:
        warnings.append(f"Out of supply, suffering attrition: {', '.join(starving)}.")
    shortages = state.last_production.get(nation.id, {}).get("shortages", {})
    if shortages:
        names = ", ".join(items[i]["name"] for i in shortages)
        warnings.append(f"Factories short of raw materials: {names}.")
    if nation.free_factories:
        warnings.append(f"{nation.free_factories} military factories idle.")
    routing = [u.designation for u in nation.units if u.routing]
    if routing:
        warnings.append(f"Routing, not answering orders: {', '.join(routing)}.")
    engaged = [u.designation for u in nation.units if u.engaged]
    if engaged:
        warnings.append(f"In contact with the enemy: {', '.join(engaged)}.")
    if state.weeks_insolvent:
        warnings.append(f"STATE BANKRUPT: week {state.weeks_insolvent} of {grace} before collapse. "
                        "Morale is falling every week and research has halted.")
    if not nation.research_project:
        warnings.append("No research project is running.")

    lines.append("WARNINGS")
    lines += [f"  ! {w}" for w in warnings] or ["  None."]
    lines += ["", "— Automated Ledger Service, Ministry of the Treasury"]
    return "\n".join(lines)


class StatusReportSystem(SimulationSystem):
    """Pushes a Weekly Status & Financial Report at the start of every week."""

    name = "status_report"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        cfg = state.config.get("reports", {})
        template = Email(
            id="status_report",
            sender=cfg.get("status_sender", "Ministry of the Treasury — Automated Ledger"),
            subject=f"Weekly Status & Financial Report — Week {state.clock.turn:03d}",
            classification="CONFIDENTIAL",
            body=build_status_body(state),
        )
        report.new_messages.append(deliver(state, template))
