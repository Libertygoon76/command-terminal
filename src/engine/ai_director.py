"""The AI Director: the Vosk Hegemony's General Staff.

Runs first in every tick, before movement resolves, so AI orders and player orders execute
simultaneously. Each AI nation has a hidden AIState: a posture (a small state machine) and a
tension value (0-100, its hostility toward Kestria), pushed up or down by the player's inbox
replies through the `ai_tension` effect.

    DEFEND ──tension ≥ probe_threshold (random)──▶ PROBE ──tension ≥ assault_threshold,
      ▲                                              │  ▲     after N weeks (random)──▶ ASSAULT
      └────────── tension < defend_threshold ◀───────┘  └── tension < assault_break_threshold
                                                            or assault_max_weeks elapsed ◀──┘
    any posture ──Kestrian strength pushed past threat_line_x ≥ threat_ratio × our front──▶ DEFEND

DEFEND   hold the line; reinforce the sector of any Kestrian incursion; pull back anything
         forward of the trenches; occasionally shuffle a reserve between staging areas.
PROBE    mass reserves on the trench line around a Schwerpunkt (re-chosen every few weeks),
         sidestep line units toward it, and probe no-man's-land (sometimes straight at a
         Kestrian position) with a chance that grows with tension.
ASSAULT  pick the weaker Kestrian flank; send an armor-led group round it via waypoints toward
         an objective in the Kestrian rear (their HQ if it is there); pin the center with probes.

Logistics apply to the AI too: every posture first pulls starving formations back into its own
supply network. The AI sees everything (it is not subject to Kestrian fog of war) and issues
orders through the same `issue_move_order` as the player, so it obeys the same rules. Every
order to a major formation (armor, HQ) may be intercepted by Kestrian SIGINT.
"""

from __future__ import annotations

from src.engine.logistics_engine import SUPPLIED
from src.engine.movement import OrderError, issue_move_order, plan_route
from src.engine.sigint import maybe_intercept
from src.engine.systems import SimulationSystem, TickReport
from src.models import ASSAULT as STANCE_ASSAULT
from src.models import DEFEND as STANCE_DEFEND
from src.models import AIState, GameState, Unit
from src.models.ai import ASSAULT, DEFEND, PROBE

ROLE_PRIORITY = {"armor": 0, "artillery": 1, "infantry": 2, "militia": 3, "hq": 4}

Order = tuple[Unit, tuple[int, int]]


def _role(state: GameState, unit: Unit) -> str:
    return next(t for t in state.catalog["units"]["units"] if t["id"] == unit.unit_type).get("role", "infantry")


def _front_rows(state: GameState, cfg: dict) -> tuple[int, int]:
    region = state.world_map.regions[cfg["front_region"]]
    return min(r[1] for r in region.rects) + 1, max(r[3] for r in region.rects) - 1  # skip border rows


def incursion(state: GameState, ai: AIState) -> list[Unit]:
    """Kestrian formations pushed past the threat line (into no-man's-land or beyond)."""
    return [u for u in state.player.units if u.x >= int(ai.config["threat_line_x"])]


def assess_threat(state: GameState, ai: AIState) -> float:
    """Kestrian strength past `threat_line_x` as a fraction of our strength holding the front region."""
    region = state.world_map.regions[ai.config["front_region"]]
    ours = sum(u.strength for u in state.nations[ai.nation_id].units if region.contains(*u.location))
    return sum(u.strength for u in incursion(state, ai)) / max(ours, 1)


class AIDirector(SimulationSystem):
    name = "ai"

    def on_tick(self, state: GameState, report: TickReport) -> None:
        for ai in state.ai_states.values():
            self.run(state, ai, report)

    # --- state machine -------------------------------------------------------

    def _set_posture(self, state: GameState, ai: AIState, posture: str) -> None:
        if posture == ai.posture:
            ai.weeks_in_posture += 1
            return
        ai.log.append(f"WK {state.clock.turn:03d}: {ai.posture} -> {posture} (tension {ai.tension:.0f})")
        ai.posture = posture
        ai.weeks_in_posture = 0
        ai.assault_flank = None
        ai.assault_group = {}

    def run(self, state: GameState, ai: AIState, report: TickReport) -> None:
        cfg = ai.config
        rng = state.rng
        lo, hi = cfg.get("tension_noise", [-2, 3])
        ai.adjust_tension(float(cfg["tension_drift"].get(ai.posture, 0)) + rng.uniform(lo, hi))

        posture = ai.posture
        if assess_threat(state, ai) >= float(cfg["threat_ratio"]):
            posture = DEFEND
        elif ai.posture == DEFEND:
            if ai.tension >= cfg["probe_threshold"] and rng.random() < float(cfg.get("probe_chance", 0.6)):
                posture = PROBE
        elif ai.posture == PROBE:
            if ai.tension < cfg["defend_threshold"]:
                posture = DEFEND
            elif (ai.tension >= cfg["assault_threshold"] and ai.weeks_in_posture >= int(cfg["assault_min_probe_weeks"])
                  and rng.random() < float(cfg.get("assault_chance", 0.5))):
                posture = ASSAULT
        elif ai.posture == ASSAULT:
            if ai.tension < cfg["assault_break_threshold"] or ai.weeks_in_posture >= int(cfg["assault_max_weeks"]):
                posture = PROBE
        self._set_posture(state, ai, posture)

        orders = self._resupply_orders(state, ai)
        taken = {u.id for u, _ in orders}
        planner = {DEFEND: self._defend_orders, PROBE: self._probe_orders, ASSAULT: self._assault_orders}[ai.posture]
        orders += [(u, t) for u, t in planner(state, ai) if u.id not in taken]

        sigint_cfg = cfg.get("sigint", {})
        sent = 0
        for unit, target in orders[: int(cfg["max_orders_per_week"].get(ai.posture, 3))]:
            try:
                route = issue_move_order(state, unit.id, target, nation_id=ai.nation_id)
            except OrderError:
                continue
            ai.log.append(f"WK {state.clock.turn:03d}: {unit.designation} -> {target} ({ai.posture})")
            # Probes and assault groups go in attacking; everything else digs in where it stops.
            attacking = unit.id in ai.assault_group or target in {u.location for u in state.player.units} \
                or target[0] < int(cfg["front_line_x"][0])
            unit.stance = STANCE_ASSAULT if attacking else STANCE_DEFEND
            intercept = maybe_intercept(state, unit, target, route.eta_weeks, sigint_cfg, sent)
            if intercept is not None:
                sent += 1
                report.new_messages.append(intercept)
                report.log.append("SIGINT intercept received.")

    # --- helpers -------------------------------------------------------------

    def _jitter(self, state: GameState, ai: AIState, x: int, y: int,
                zone: set[str] | None = None) -> tuple[int, int] | None:
        """Randomize a target a little and snap it to passable ground (inside `zone` if given)."""
        j = int(ai.config.get("position_jitter", 2))
        world = state.world_map
        for _ in range(10):
            tx, ty = x + state.rng.randint(-j, j), y + state.rng.randint(-j, j)
            region = world.region_at(tx, ty)
            if region is None or not 1 <= tx <= world.width - 2:
                continue
            if zone is None or region.id in zone:
                return tx, ty
        return None

    @staticmethod
    def _zone(ai: AIState) -> set[str]:
        return set(ai.config["home_regions"]) | {ai.config["front_region"]}

    def _idle(self, state: GameState, ai: AIState) -> list[Unit]:
        units = [u for u in state.nations[ai.nation_id].units
                 if u.active_order is None and not u.engaged and not u.routing]
        state.rng.shuffle(units)  # unpredictable within each priority class
        return sorted(units, key=lambda u: ROLE_PRIORITY.get(_role(state, u), 5))

    def _resupply_orders(self, state: GameState, ai: AIState) -> list[Order]:
        """Pull starving formations back to the nearest cell of our own supply network."""
        info = state.supply_networks.get(ai.nation_id)
        if not info or not info["network"]:
            return []
        threshold = float(ai.config.get("resupply_threshold", 25))
        orders = []
        for unit in state.nations[ai.nation_id].units:
            if unit.supply >= threshold or unit.supply_state == SUPPLIED or unit.engaged or unit.routing:
                continue
            if unit.active_order and unit.active_order.target in info["network"]:
                continue  # already falling back
            target = min(info["network"], key=lambda c: abs(c[0] - unit.x) * 0.5 + abs(c[1] - unit.y))
            orders.append((unit, target))
            ai.assault_group.pop(unit.id, None)
        return orders

    # --- posture planners ----------------------------------------------------

    def _defend_orders(self, state: GameState, ai: AIState) -> list[Order]:
        cfg = ai.config
        rng = state.rng
        fx0, fx1 = cfg["front_line_x"]
        y0, y1 = _front_rows(state, cfg)
        orders: list[Order] = []
        idle = self._idle(state, ai)
        intruders = incursion(state, ai)

        for unit in idle:
            if unit.x < fx0:  # forward of our trenches: pull back to the line
                target = self._jitter(state, ai, fx1, unit.y, self._zone(ai))
                if target:
                    orders.append((unit, target))
        if intruders:
            # Reactive defense: reserves march to the threatened sector.
            threat_y = round(sum(u.y for u in intruders) / len(intruders))
            for unit in idle:
                if unit.x > fx1 + 2 and _role(state, unit) != "hq":
                    target = self._jitter(state, ai, rng.randint(fx0, fx1), threat_y, self._zone(ai))
                    if target:
                        orders.append((unit, target))
        elif rng.random() < float(cfg.get("reserve_move_chance", 0.35)):
            reserves = [u for u in idle if u.x > fx1]
            if reserves:
                sx0, sx1 = cfg["staging_x"]
                target = self._jitter(state, ai, rng.randint(sx0, sx1), rng.randint(y0, y1), self._zone(ai))
                if target:
                    orders.append((reserves[0], target))
        return orders

    def _probe_orders(self, state: GameState, ai: AIState) -> list[Order]:
        cfg = ai.config
        rng = state.rng
        fx0, fx1 = cfg["front_line_x"]
        y0, y1 = _front_rows(state, cfg)
        spread = int(cfg.get("focus_spread", 4))
        if ai.focus_y is None or ai.weeks_in_posture % int(cfg.get("focus_shift_weeks", 4)) == 0:
            ai.focus_y = rng.randint(y0 + spread, y1 - spread)
            ai.log.append(f"WK {state.clock.turn:03d}: Schwerpunkt shifts to row {ai.focus_y}")
        orders: list[Order] = []
        idle = self._idle(state, ai)

        probe = self._probe(state, ai, idle)
        if probe:
            orders.append(probe)
            idle.remove(probe[0])

        for unit in idle:
            if fx0 <= unit.x <= fx1:
                continue  # already on the line
            target = self._jitter(state, ai, rng.randint(fx0, fx1), ai.focus_y + rng.randint(-spread, spread),
                                  self._zone(ai))
            if target and plan_route(state, unit, target):
                orders.append((unit, target))

        # Sidestep: pull a line formation that is far from the Schwerpunkt toward it.
        busy = {u.id for u, _ in orders}
        far = [u for u in idle if fx0 <= u.x <= fx1 and abs(u.y - ai.focus_y) > spread and u.id not in busy]
        if far and rng.random() < float(cfg.get("sidestep_chance", 0.45)):
            unit = rng.choice(far)
            step = rng.randint(3, 6) * (1 if ai.focus_y > unit.y else -1)
            target = self._jitter(state, ai, unit.x, unit.y + step, self._zone(ai))
            if target:
                orders.append((unit, target))
        return orders

    def _probe(self, state: GameState, ai: AIState, idle: list[Unit]) -> Order | None:
        """With a chance that grows with tension, send a line infantry unit into no-man's-land."""
        cfg = ai.config
        fx0, fx1 = cfg["front_line_x"]
        chance = max(0.0, ai.tension - cfg["probe_threshold"]) * float(cfg.get("probe_chance_per_tension_point", 0.012))
        line = [u for u in idle if fx0 <= u.x <= fx1 and _role(state, u) in ("infantry", "militia")]
        if not line or not state.player.units or state.rng.random() >= chance:
            return None
        prober = state.rng.choice(line)
        victim = min(state.player.units, key=lambda k: abs(k.x - prober.x) + abs(k.y - prober.y))
        if state.rng.random() < 0.5:
            return prober, victim.location  # a raid straight at their trenches
        return prober, ((prober.x + victim.x) // 2, victim.y)  # into no-man's-land

    def _assault_orders(self, state: GameState, ai: AIState) -> list[Order]:
        cfg = ai.config
        rng = state.rng
        flanks = cfg["flanks"]
        if ai.assault_flank is None:
            # Go round the flank where Kestria is weakest (ties broken at random).
            def strength(name: str) -> float:
                r0, r1 = flanks[name]["rows"]
                return sum(u.strength for u in state.player.units if r0 <= u.y <= r1) + rng.random()

            ai.assault_flank = min(flanks, key=strength)
            ai.log.append(f"WK {state.clock.turn:03d}: ASSAULT on the {ai.assault_flank} flank")
        flank = flanks[ai.assault_flank]
        waypoints = [tuple(w) for w in flank["waypoints"]] + [self._objective(state, flank)]

        orders: list[Order] = []
        idle = self._idle(state, ai)
        units = {u.id: u for u in state.nations[ai.nation_id].units}
        # Recruit the assault group: armor first, then infantry; HQ and artillery stay back.
        # Recruits are re-tasked even if they are still executing an older (PROBE) order.
        recruits: set[str] = set()
        candidates = sorted(
            (u for u in units.values() if not u.engaged and not u.routing and u.id not in ai.assault_group
             and _role(state, u) in ("armor", "infantry") and u.supply_state == SUPPLIED),
            key=lambda u: (ROLE_PRIORITY[_role(state, u)], u.active_order is not None, rng.random()),
        )
        for unit in candidates:
            if len(ai.assault_group) >= int(cfg.get("assault_group_size", 3)):
                break
            ai.assault_group[unit.id] = 0
            recruits.add(unit.id)
        for unit_id, index in list(ai.assault_group.items()):
            unit = units.get(unit_id)
            if unit is None or unit.engaged or unit.routing or (unit.active_order is not None and unit_id not in recruits):
                continue
            while index < len(waypoints) - 1 and abs(unit.x - waypoints[index][0]) + abs(unit.y - waypoints[index][1]) <= 2:
                index += 1  # reached this waypoint: head for the next
            ai.assault_group[unit_id] = index
            wx, wy = waypoints[index]
            target = self._jitter(state, ai, wx, wy) if index < len(waypoints) - 1 else (wx, wy)
            if target:
                orders.append((unit, target))

        # Pin the center so Kestria cannot shift reserves to the threatened flank.
        pin = self._probe(state, ai, [u for u in idle if u.id not in ai.assault_group and u.id not in recruits])
        if pin:
            orders.append(pin)
        return orders

    @staticmethod
    def _objective(state: GameState, flank: dict) -> tuple[int, int]:
        region = state.world_map.regions[flank["objective_region"]]
        hq = [u for u in state.player.units if _role(state, u) == "hq" and region.contains(*u.location)]
        if hq:
            return hq[0].location
        towns = [f for f in state.world_map.features if region.contains(f.x, f.y)]
        if towns:
            return towns[0].x, towns[0].y
        x0, y0, x1, y1 = region.rects[0]
        return (x0 + x1) // 2, (y0 + y1) // 2
