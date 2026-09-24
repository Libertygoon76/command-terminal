# COMMAND TERMINAL — Game Design Document

> Living document. Update it whenever a system is designed, changed, or cut.
> Last major revision: Expansion 1.1 (the living world: foreign powers & lend-lease, city management, local news, the Vosk Hotline), 2026-09-24. Before that, Phase 8 (the home front: epidemics and natural disasters with CRITICAL EMERGENCY choices; chain of command & insubordination, electronic warfare, scorched earth & combat engineers, victory by capitulation, save/load), 2026-09-24.

---

## 1. Vision

**Command Terminal** is a hardcore grand-strategy and nation-management **simulator first, game second**.
It aims to be far deeper than *Risk*: armies need food, fuel and ammunition; economies run on real
production chains; and every decision ripples through a simulated state.

The player is the **Head of State** (default title: *Lord Protector*) running their nation from a
**secure, classified government terminal**. There is no god-camera and no floating 3D map. Everything
is read through the terminal: dashboards, ledgers, ASCII territory maps and — most importantly — a
**classified email inbox** where ministers, generals, spies and foreign powers report to you.

### Design pillars

1. **Information is the interface.** You never see the world directly — only reports about it.
   Reports can be late, incomplete, or wrong (fog of war via intelligence quality).
2. **Logistics decide wars.** Armies do not spawn. They are recruited, trained, equipped and
   supplied. Cut supply lines and an army starves, deserts and collapses.
3. **Everything costs something.** Every lever (taxes, conscription, edicts) trades one resource
   or constituency against another. No free wins.
4. **Weekly consequences.** The world advances in discrete ticks (1 turn = 1 week). All math runs
   on the tick, so outcomes are deterministic given the same state and random seed.
5. **Data-driven.** Balance numbers, units, buildings and event text live in JSON so designers can
   tune and write content without touching engine code.

### Aesthetic

Military / government operating system: DEFCON, HighFleet, Cold-War mainframes, hacker terminals.

- Phosphor-green on near-black, amber for warnings, red for critical/TOP SECRET.
- Box-drawing borders, uppercase headings, classification banners, terse bureaucratic prose.
- Boot sequence with hardware checks, crypto handshake and biometric "authentication".
- Sound (future, optional): relay clicks, teletype chatter.

---

## 2. Locked Design Decisions

Confirmed with the project owner after Phase 1. Do not change without asking.

| Topic | Decision |
|-------|----------|
| **Setting** | Fictional **1984 Cold-War-era** world with **WWI/WWII-style industrial warfare** (trenches, artillery, armor). Gritty and bureaucratic. |
| **Player nation** | Always the **Commonwealth of Kestria** (capital Aldmark). **No nation-selection screen.** All depth goes into simulating one nation. |
| **Rival** | **Vosk Hegemony** (capital Karzan), east across the contested **Frontier**. Neutral sea lane: **Iren Straits**. |
| **Win / loss** | **Survive — and, since Phase 8, win**: force the Vosk Hegemony to capitulate (§4.26). Three brutal loss conditions, below. |
| **Fog of war** | **Yes, always.** Intelligence arrives as **ranges with a stated certainty %**, and reports can be **entirely wrong** while still claiming confidence. |

### Loss conditions (checked at the end of every turn)
| Cause | Trigger (`data/config.json → fail_states`) |
|-------|----------------------------------------------|
| **Armed revolution** | Civil morale ≤ `revolution_morale` (0) |
| **Military coup** | Military morale ≤ `coup_military_morale` (10) |
| **State collapse** | Treasury negative for `bankruptcy_grace_weeks` (8) consecutive weeks, **or** treasury ≤ `collapse_debt_limit` (−1,500,000) |

On failure the engine pushes a pinned, undeletable **"CRITICAL ALERT: SYSTEM PURGE"** dispatch
(`data/events/system_alerts.json`), and the terminal locks. A non-dismissable **PROTOCOL ZERO**
screen shows the purge text, and the only commands left are **[R] Restart campaign** (a fresh
government on a fresh desktop) or **[Q] Exit**. `[P6]`

---

## 3. Core Loop

```
 ┌──────────────┐    ┌────────────────┐    ┌──────────────────┐    ┌──────────────┐
 │ Read inbox   │ -> │ Review dash-   │ -> │ Reply; issue     │ -> │ ADVANCE WEEK │
 │ (reports,    │    │ boards + War   │    │ move orders on   │    │ (n / button) │
 │  SIGINT)     │    │ Room map       │    │ the map          │    │              │
 └──────────────┘    └────────────────┘    └──────────────────┘    └──────┬───────┘
        ^                                                                 │
        │  AI plans → movement & clashes → recon → logistics → economy →  │
        │  events → weekly status report → fail-state check               │
        └─────────────────────────────────────────────────────────────────┘
```

---

## 4. Systems

Status legend: `[P1]`/`[P2]` built in that phase · `[STUB]` placeholder · `[PLANNED]` design only.

### 4.1 Time & Tick Engine `[P1]`
- 1 turn = 1 week (`days_per_turn`).
- `TickEngine.advance()` moves the clock forward, then runs each `SimulationSystem` in a fixed
  order, collecting a `TickReport` (log lines, new messages, game over).
- Seeded RNG lives in `GameState.rng`. Set `random_seed` in config, or run `python main.py --seed N`,
  for a reproducible campaign.
- After a fall, `advance()` raises `GameOverError`; while a CLASSIFIED DILEMMA is unanswered it raises
  `DilemmaPendingError` (§4.21).
- **Scale** `[P7]` (`tick_engine.MILES_PER_CELL`, config `map.miles_per_cell`): **one grid step (a row) =
  10 miles**. Terminal characters are twice as tall as they are wide, so a column is half a step (5 miles,
  `map.column_scale` 0.5), which keeps the map in proportion. Every speed is authored in **miles per day**
  and converted by `tick_engine.cells_per_turn()`.

### 4.2 Nation Management `[P2 · P6]`
- **Treasury**: can go negative. Debt pays 1% interest per week.
- **Taxation** `[P6]` (`config.json → economy.tax_policies`; `[` / `]` on the Economy screen):

| Policy | Rate | Civil morale per week |
|--------|------|-----------------------|
| Low | 12% | +0.6 |
| Normal | 18% | 0 |
| High | 26% | −0.9 |
| Oppressive | 36% | −2.2 |

  Survey of 40-week campaigns: Normal survives with a growing treasury; High doubles the treasury
  but leaves civil morale near 3 (one bad week from revolution); Oppressive ends in revolution every
  time. Inbox effects can still nudge the rate within a policy.
- **Bankruptcy** `[P6]`: whenever the treasury is **at or below 0**, salaries go unpaid. Civil morale
  falls 4 and military morale 6 every week (`economy.bankruptcy`), research halts, and the status bar
  flashes **⚠ STATE BANKRUPT**. After 8 consecutive weeks the state collapses.
- **Population** and **Manpower** (the recruitable pool; spent on recruitment and replacements).
- **Civil morale** 0–100: sets productivity (75% at 0 → 100% at 100). **At 0 there is an armed revolution.**
- **Military morale** 0–100: loyalty of the armed forces. At 10 or below there is a coup.
- `[PLANNED]` Edicts, rationing, conscription laws, factions (§7.4).

### 4.3 Economy & the Military Industrial Complex `[P2 ledger · P5 war production]`
- **Weekly ledger** (`economy_engine.compute_ledger`, run for **every** nation, so the AI pays its own way):
  - income: `tax = population × tax_rate × 0.042 × productivity(morale)`, plus **trade & industry**
    (`nations.json → trade_income` × productivity; Kestria 11,000/week) `[P6]`, plus **overseas trade**
    through every OPEN port (`world.json` port `trade`; Kestria 13,000/week across five ports) `[P7]`.
    A port under naval **blockade** earns nothing (§4.18);
  - expenses: civil administration, armed forces pay, military production (1,100 CR per assigned
    factory), the **active research project** `[P6]`, and debt interest.
  - At Normal taxes with the default 13 factories, Kestria nets about +10,900 CR/week. Every extra
    factory or research project eats into that.
- **Domestic resource output** (`nations.json → resource_output`, placeholder): steel, munitions,
  fuel, coal, and so on, added to resource stockpiles every week. Factories consume these as inputs.
- **Military factories** (`src/engine/production.py`): each nation owns `military_factories`
  (Kestria 24, Vosk 32). On the **Economy screen** the player assigns them to **production lines**,
  one per item in `equipment.json`:
  - An item is **LOCKED** until its `requires_tech` is known (the tech tree's `known_at_start` for now).
  - Weekly output per factory is `batch × 7 / factory_days × line efficiency`. A new line starts at
    **50%** efficiency and gains **+10% per week** up to 100%. Closing a line resets it, so constant
    retooling is costly.
  - Output is capped by raw inputs (`cost` per unit). A shortage shows on the Economy screen and in
    the weekly report.
  - Output goes into the nation's **national stockpile** of finished equipment.
- **The AI** rebalances its factories every week toward its formations' worst shortages.

### 4.4 Movement & Orders `[P4]`
Module: `src/engine/movement.py`.
- **Orders.** Each `Unit` has an `active_order` (`MoveOrder(target, issued_turn)`) or `None`,
  a `status` (HOLDING / MOVING / ENGAGED), and `move_points` carried over between weeks.
  `issue_move_order()` validates ownership, bounds, sea and reachability. The player and the AI use
  the same function.
- **Speed** `[P7]`: `speed_mpd` in `units.json` is the sustained pace in **miles per day** over open
  plains; `truck_mpd` is added in proportion to the formation's trucks, if it has fuel. Movement points
  per week = miles per week ÷ 10.

| Formation | Pace | Rows (×10 mi) per week, open plains |
|-----------|------|------|
| Infantry division | 4 mi/day on foot, +3 with all its trucks | 2.8 on foot · 4.9 motorised |
| Armored brigade | 12 mi/day | 8.4 |
| Artillery regiment | 3.5 + 3 by truck | 2.45 – 4.55 |
| Militia battalion | 3.5 | 2.45 |
| HQ / Logistics | 4 + 4 by truck | 2.8 – 5.6 |
| Destroyers / battleships / submarines | 90 / 70 / 50 mi/day | 63 / 49 / 35 (at sea) |

  A road multiplies the distance covered by ~1.8, rail by ~2.9. Low supply (<25%) halves speed; the
  weather multiplies it (§4.20).
- **Pathfinding.** A* over 4-neighbour cells (land, or open water for warships), cached per
  (domain, start, target): costs never change, so a marching column never needs a fresh search. Step cost:
  - along **rail / road / destroyed rail** (`world.json → transport`), a flat
    `map.transport_cost` (0.35 / 0.55 / 0.8) that ignores terrain;
  - otherwise `1 / terrain movement` (mountains ×2.5, marsh/mud ×2, urban ×1.4, river ×1.25);
  - **trench lines** cost an extra ×1.8 (`map.obstacle_cost`);
  - a column is half a row (`map.column_scale` 0.5): 1 row = 10 miles, 1 column = 5 miles.
- **Simultaneous resolution.** Every moving unit steps one cell per round, in lockstep, until
  nobody can afford their next step. Unspent points carry over.

### 4.5 Skirmish Detection (pre-combat) `[P4]`
- After every movement round the engine checks each mover against hostile units. **Contact** means
  the same cell, **adjacent** (scaled distance ≤ 1: one row, or up to two columns), or **crossed paths**
  (the two units swapped cells in the same round).
- Both units **halt**, their orders are cleared, and their status becomes **ENGAGED**. A TOP SECRET
  **"CRITICAL: BORDER CLASH at Grid X-Y"** dispatch is pushed to the inbox
  (text in `data/events/generated.json → clash`).
- An engagement persists while the pair stays in contact range. A unit already engaged can be
  ordered away (withdraw) without being re-halted by the same enemy. Casualties come in Phase 5.

### 4.6 Logistics — the arteries of war `[P4]`
Module: `src/engine/logistics_engine.py`, config `config.json → logistics`.
- **Sources** `[P7]`: every friendly settlement in a region the nation owns, each reaching its own
  distance (`source_range_by_type`): capital 18, city / industrial centre / port 16, market town 7. A
  **blockaded port supplies nothing**. The Vosk also draw on their **home map edge** (the hinterland
  beyond the eastern edge of the sheet); Kestria's west is now open ocean.
- **Warships** are supplied by sea: SUPPLIED within `naval.supply_range` (45 rows of sailing) of a
  friendly, unblockaded harbour, otherwise OVEREXTENDED.
- **Tracing:** a multi-source Dijkstra over the same costs as movement, so **roads and rail carry
  supply far** and mountains and marsh strangle it. An in-supply **HQ / Logistics** formation is a
  forward depot, extending the net by `hq_range`.
- **Enemy territory:** supply cannot use an enemy's roads or rail, and pays terrain cost ×
  `hostile_territory_factor` (1.5). Armies can race down enemy roads, but their supply cannot follow.
- **Zones of control:** every cell within `zoc_radius` of an enemy **land** formation (warships never cut land supply). A supply path may end
  in a ZOC cell but can never pass *through* one. Enemy-occupied cells are fully blocked.
- **States** (recomputed weekly, `Unit.supply_state`):

| State | Condition | Effect |
|-------|-----------|--------|
| SUPPLIED | path within range | delivery 35%→15% per week, falling with distance |
| OVEREXTENDED | path exists but too long | no delivery |
| ISOLATED | no path at all | no delivery + "URGENT: Isolated" dispatch |

- **Consumption** per week: base 8%, +4% while moving, +12% while engaged.
- **At 0% supply:** attrition of 4% of manpower per week (floored at 10% of establishment), −5
  morale per week, and the unit **cannot advance**. It may only move to a cell inside its own
  supply network (fall back).
- **Map overlay (`s`):** blue shows our supply network and red shows known enemy zones of control.
  The overlay only reflects what our staff can know: hidden enemies' zones are not drawn, and the
  holes they punch in the net are painted over.

### 4.7 Vosk AI Director `[P4]`
Module: `src/engine/ai_director.py`, config `data/ai.json`. It runs **first** in every tick, so its
orders resolve at the same moment as the player's. It has perfect information (it is not subject
to Kestrian fog of war).
- **Tension** (0–100, hidden): drifts each week by posture, plus noise. The inbox moves it through the
  hidden `ai_tension` effect: leaking intel +6, rejecting their protest +8, apologizing −10,
  defying the ultimatum +15, successful talks −15, and so on.
- **State machine:**

| Posture | Behaviour | Transitions |
|---------|-----------|-------------|
| **DEFEND** | Holds the line. Reserves reinforce the sector of any Kestrian incursion; units forward of the trenches pull back; a reserve is occasionally reshuffled. | → PROBE at tension ≥ 40 (random) |
| **PROBE** | Masses reserves on its trench line around a **Schwerpunkt** row (re-chosen every 4 weeks), sidesteps line units toward it, and probes no-man's-land. The chance of a probe grows with tension, and half of probes go straight at a Kestrian position. | → ASSAULT at tension ≥ 65 after 3+ weeks (random); → DEFEND below 25 |
| **ASSAULT** | Picks the **weaker Kestrian flank** (north or south). Sends an armor-led group of up to 3 formations round it through configured waypoints toward a rear objective: the Kestrian HQ in that region, else a town. Pins the center with probes. | → PROBE when tension < 48 or after 8 weeks |
| any | — | → DEFEND when Kestrian strength past x=124 reaches 35% of Vosk front strength |

- **Logistics-aware:** each posture first pulls starving, out-of-supply formations back to the
  nearest cell of its own supply network.
- **Combined arms** `[P7]`: artillery is kept 3 columns *behind* its trench line (in support range, out
  of contact). The **Vosk admiralty** patrols home waters in DEFEND; in PROBE / ASSAULT it sends destroyers
  and submarines to **blockade** the richest Kestrian port they can reach and battle squadrons to
  **bombard** coastal battles, and any squadron hunts enemy ships within 20 rows. The **Vosk air staff**
  puts its wings over the sectors where its army is fighting, else over the Frontier.
- **Unpredictable:** target jitter, a shuffled choice of unit within each priority class, random
  Schwerpunkt and flank tie-breaks, and probabilistic transitions. Everything comes from the campaign
  RNG, so a seeded campaign is fully reproducible.
- Run `python main.py --reveal` to lift the fog of war and see the AI's posture, tension and
  Schwerpunkt in the Sector Readout.

### 4.8 SIGINT — signals intelligence `[P4]`
Module: `src/engine/sigint.py`, config `ai.json → sigint`, text `generated.json → sigint`.
- Whenever the AI orders a **major formation (Armor or HQ)** to move, there is a 25% chance (+15% with
  the LANTERN network) of an intercept, at most 2 per week.
- It arrives at the start of the week as a **"TOP SECRET: SIGINT"** dispatch that names the net,
  frequency, unit and destination sector, with a grid reference accurate to about ±3 columns.
  Segments marked `[[...]]` in the templates are **[REDACTED]** at random (40%). Confidence is stated,
  and the text warns that intercepts may be deception.

### 4.9 Resupply Pipeline `[P5]`
Module: `logistics_engine.py`, config `logistics.resupply`.
- **Physical inventory.** Each formation carries `equipment_inventory`. Its **establishment** is the
  template `loadout` scaled by current strength (a half-strength division needs half the rifles).
- **Fuel** (`fuel_drums`) burns every week by status (`units.json → fuel_use`: armor holding 30,
  moving 180, engaged 60). A formation that needs fuel and has none moves at 25% speed, and its tanks
  cannot fire.
- **Resupply.** Every SUPPLIED formation (not routing) draws from the national stockpile toward
  establishment. Engaged formations are served first, then the emptiest. Per-week caps:
  ammunition and consumables `60% → 25%` of establishment, falling with supply-line length;
  weapons and vehicles 12%. **An empty depot delivers nothing.** Isolated and overextended formations
  get nothing at all.
- **Replacements and rest.** Supplied formations out of combat regain 4% of establishment per week in
  men from the manpower pool, and +2 morale per week (up to 85).
- The generic **supply %** still stands for food, spares and general stores (Phase 4 rules).

### 4.10 Tactical Combat — the Meatgrinder `[P5]`
Module: `src/engine/combat_engine.py`, config `config.json → combat`.
- **Battles.** Every connected group of formations in contact (the engagement graph) is a named
  multi-week **Battle** ("Battle of the Frontier", "Second Battle of Greywater" ...). Formations join
  as they make contact. Each battle keeps a true ledger of casualties, equipment lost and ammunition
  spent per side.
- **Weekly round.** Firing is simultaneous.
  - **Firepower is physical.** Each weapon fires only while its ammunition lasts
    (`ammo_per_week × stance.ammo`, drawn from the unit's own inventory). Tanks also need
    `fuel_per_week`. Each firing weapon adds `soft_attack` (vs men) and `hard_attack` (vs armor). A
    dry formation fights with bayonets: near zero.
  - `damage = Σ firepower × stance.attack × (0.5 + morale/200) × supply factor × noise(0.8–1.2)`.
  - `applied = soft × (1 − H) + hard × H`, where H is the armored share of the target side's strength.
  - Casualties to each enemy formation: `applied × 1.6 × (its share of strength) / defense`.
  - `defense = terrain defense × fortification × stance.defense × Kevlar`. **Fortification** ×1.6
    applies within 2 columns of the formation's **own** trench line (a captured trench faces the wrong
    way), and ×1.3 in urban terrain. **Never in ASSAULT stance**: attackers have gone over the top.
  - Casualties **destroy equipment** (`loss_rate` × casualty fraction: rifles 1.0, trucks 0.3,
    howitzers 0.4, tanks 0.6) and cost morale (2 per 1% of strength lost, +1.5 combat stress,
    +5 if ammunition-starved).
- **Stances** (`units.json → stances`; set per formation with **`t`** in the War Room):

| Stance | Attack | Defense | Ammo use | Notes |
|--------|--------|---------|----------|-------|
| DEFEND | ×0.9 | ×1.5 | ×0.7 | Default. Fortification applies. |
| ASSAULT | ×1.4 | ×0.7 | ×1.5 | No fortification. The formation takes the ground if the enemy breaks. |
| WITHDRAW | ×0.3 | ×1.1 | ×0.5 | Falls back 1 cell a week toward HQ, breaking contact. |

  The AI picks stances each round by **local superiority**: ASSAULT at ≥1.5:1, WITHDRAW below 0.6:1,
  when morale is under 30, or when ammunition is under 15%; otherwise DEFEND. Units the AI orders
  forward (probes, assault groups) go in on ASSAULT.
- **Routing.** A formation **breaks** when morale < 15 or strength < 10% of establishment. It takes
  10% bonus casualties, becomes **ROUTING** (it ignores orders for 2 weeks, then rallies), and falls
  back 2 cells toward its HQ. **The victorious enemy takes its cell.** At 0 men it is **destroyed**
  and leaves the order of battle.
- **Balance** (30 AI campaigns × 30 weeks): about 5 battles per campaign, lasting 3–11 weeks (mean 3.6).
  Attackers lose about **1.6 men per defender**, and defended trench lines hold in about 85% of
  battles.

### 4.11 Battle Reporting `[P5]`
- **SITREP: ONGOING COMBAT** (weekly, per battle): our casualties (exact), enemy casualties
  (**estimate range**), the strength, morale, ammunition fill, stance and status of each of our
  formations, ammunition shortages, and breaks and withdrawals.
- **AFTER ACTION REPORT** (when nobody in the battle is still in contact): VICTORY, DEFEAT or
  INCONCLUSIVE (the side still holding the field, i.e. not routed, withdrawing or destroyed, wins),
  total casualties (ours confirmed, theirs estimated), our equipment lost and ammunition expended,
  and the fate of each of our formations. Victory gives +3 national military morale; defeat −4.

### 4.12 Recruitment & Training `[P6]`
Module: `src/engine/recruitment.py`, config `config.json → recruitment`, UI on the **Military** screen.
- **Raise** any formation type (`r` on the RECRUITMENT table). Its `recruit_cost` (CR) and `manpower`
  are paid **up front**, then it trains for `training_weeks` (infantry 8, armor 14, artillery 10,
  militia 2, HQ 6). Names follow the template's `name_pattern` ("4th Kestrian Infantry Division") and
  designations continue the series (K-08, K-09 ...).
- **Musters** at the capital (Aldmark, grid 014-022), or `nations.json → muster_point`, with full
  manpower, green morale (60), and only **25% of its establishment**, issued from the national
  stockpile. The logistics pipeline fills the rest: ammunition within a week or two, **weapons at 12%
  per week**. The Military screen's READY column (the worse of weapons and ammunition fill) shows
  when it is fit for the line. The player receives a "New Formation Ready" dispatch.
- **Cancel** a formation in training (`x` on the IN TRAINING table): all manpower is returned, plus half
  the cost.
- **The AI** raises replacements (infantry, sometimes armor) whenever its army falls below 95% of its
  starting strength and it holds twice the cost in reserve.

### 4.13 Research & Development `[P6]`
Module: `src/engine/research.py`, data `tech_tree.json`, UI on the **Research** tab (`5`).
- **One project at a time.** Highlight an AVAILABLE tech and press Enter (or `r`); `x` stops it. The
  project costs its `weekly_cost` every week (it appears on the ledger) and completes after `weeks` weeks.
  **Progress is kept** when you switch. **No progress while bankrupt.**
- **On completion:** a SECRET **"R&D BREAKTHROUGH: <tech>"** dispatch arrives, and:
  - its `unlocks` become producible (the Economy screen's LOCKED lines open);
  - unit templates with `upgrades` for the tech grow their establishment (Kevlar vests for every man,
    medium tanks and 120mm shells for armored brigades, APFSDS rounds, night vision), so the resupply
    pipeline starts delivering the new kit, once factories build it;
  - `effects` apply: Assembly-Line Retooling gives +10% output on all factories; Defense-in-Depth
    Doctrine gives +10% defense in DEFEND stance.
- **Equipment effects in combat:** Kevlar reduces casualties by 15% when every man has a vest; carrying
  at least half the establishment of APFSDS rounds gives +35% hard attack; night vision gives +10% to
  all fire.
- **The AI** researches too (cheapest available first), which changes what its factories can build.

### 4.14 Classified Inbox & Event System `[P2]`
The inbox is the heart of the game. Module: `src/engine/event_manager.py`.

**Email template schema** (`data/events/emails.json`):

| Field | Meaning |
|-------|---------|
| `id`, `sender`, `subject`, `classification`, `body` | Content. `body` supports `{nation}`, `{leader_title}`, `{capital}`, `{date}`, `{turn}` and intel placeholders. |
| `arrives_turn` | Schedules the email at game start. Leave it out for follow-up-only templates. |
| `on_arrival` | Effects applied the moment the email is delivered (e.g. a failed op's fallout). |
| `options[]` | Replies: `{id, label, effects, follow_ups}`. |
| `deadline_weeks` + `on_expire` | If unanswered by then, `on_expire` (same shape as an option) is applied automatically. **Silence is a decision.** |
| `intel` | Fog-of-war estimates rendered into the body (see 4.15). |
| `pinned` | Always on top and never archived (used by SYSTEM PURGE). |

**Effects** (`src/engine/effects.py`, all deltas): `treasury`, `manpower`, `population`,
`morale`, `military_morale`, `tax_rate` (a fraction), `stockpiles{resource: n}`, and `flags{name: value}`.
Unknown keys, unknown resources, and broken follow-up references are rejected **at startup**
with a message naming the email.

**Event chains**: `follow_ups: [{"email": id, "delay_weeks": n}]` or a weighted gamble
`{"delay_weeks": 3, "chance": 0.8, "outcomes": [{"email": a, "weight": 65}, {"email": b, "weight": 35}]}`.
The outcome is rolled when the reply is sent and delivered `delay_weeks` later. The player is not told in advance.

**Lifecycle**: delivered (unread) → read → replied / expired (archived). A reply goes through a
confirmation dialog, applies its effects immediately, shows the consequences in the email,
and cannot be sent twice. Replies with a deadline show "REPLY BY WK n", or "DUE THIS WEEK".

**Generated dispatches**:
- *Weekly Status & Financial Report* (`reports.py`): pushed at the start of every week with
  income, expenses, net, treasury, civil/military morale, pending replies, and warnings.
- *CRITICAL ALERT: SYSTEM PURGE* (`fail_states.py`) on a fall.

### 4.15 Fog of War `[P2 estimates · P3 units · P4 active]`
- `src/engine/intel.py`. A spec is `{"value": n}` or `{"nation": "vosk", "stat": "manpower", "fraction": f}`,
  plus `accuracy` (0–1).
- Rendered as `LOW – HIGH (CERTAINTY xx%)`. The range width scales with (1 − accuracy), and honest
  estimates are noisy around the truth.
- **Misinformation**: with chance `misinformation_base_chance × (1 − accuracy) × 2` (or a per-spec
  `misinformation_chance`), the estimate is centred on a false value (×0.25–0.6 or ×1.6–3.0)
  **while still claiming the same certainty**. The truth is stored hidden in `email.intel_truth`,
  for later "the report was wrong" reveals.

### 4.16 War Room — Tactical Situation Map `[P3 · P4 orders · P5 stances]`
A digitized staff situation map in the style of WWII Eastern Front and Desert Storm operational
maps: terrain, national territory washes, trench lines, rail, and NATO-style unit symbols.

**Map data: an exact character grid** (`data/map/world.json`, model `src/models/world_map.py`)
- `width` × `height` (**240 × 80** since Phase 7: 1,200 × 800 miles) terminal cells. Coordinates are
  **x = column, y = row**, with 0,0 at top-left.
- `base`: the rendered background, `height` rows of exactly `width` characters. It holds borders
  (`│─┼…`), terrain glyphs (`^` mountains, `≈` river/marsh, `#` urban, `.` plains), rail `═║`,
  trenches `┆`, settlements `★` capital, `◉` city, `⊕` port, plus labels, compass and scale bar.
- `regions[]`: `{id, name, owner, terrain, key, label:[x,y], rects:[...]}`. Since Phase 7 the exact
  per-cell region map is `region_rows` (one letter per cell, `~` = sea); `rects` are the coarse blueprint
  outline. `WorldMap.region_at(x, y)`, `in_region`, `owner_at`, `is_sea`, `is_coastal`, `sea_zone_at`
  and `is_national_border` are exact per-cell lookups.
- `sea_zones[]`: named waters (Northern Sea, Western Ocean, Iren Straits, Gulf of Sevrask) for readouts
  and battle names.
- `terrain_types{}`: glyph, `movement` and `defense` multipliers, and a description. Shown in the readout
  now, and used by logistics and combat later.
- `features[]`: settlements with exact X/Y: `capital` ★, `city` ◉, `industrial` ▣, `port` ⊕ (with
  `trade` and a `harbour` sea cell), `town` ○.
- Every row must be exactly `width` **single-cell** characters. Emoji and wide glyphs are forbidden
  (checked in tests). `tools/generate_world_map.py` can regenerate the whole map, but it **overwrites** the file.

**Units on the map** (`src/models/military.py`, data `data/orbat.json`)
- `Unit{id, designation, name, nation, type, location:[x,y], strength, morale, supply, stance, commander}`.
  Each `Nation` holds its `units`. Locations are validated at load: in bounds, not at sea, room for the symbol.

**Terminal NATO symbology** (`symbol` per template in `units.json`)

| Symbol | Type |
|--------|------|
| `[X]` | Infantry |
| `[O]` | Armor |
| `[•]` | Artillery |
| `[H]` | HQ / Logistics |
| `[m]` | Militia |
| `[D]` | Destroyer Flotilla (8 destroyers) |
| `[B]` | Battleship Squadron (2 battleships + 4 destroyer escorts) |
| `[U]` | Submarine Wolfpack (6 boats) |
| `[*]` | **Stack**: 2+ formations in the same cell, or whose symbols would overlap |

- A symbol is 3 cells wide and centred on the unit's `location` (x−1 … x+1).
- Friendly (player) symbols are **cyan**, hostile symbols **red**. A stack holding both sides is **amber** (contact/engagement).
  Colors are in config `map`.
- Overlap rule (`engine/map_overlay.py`): any unit whose symbol would touch an existing marker joins
  it as a stack. The layout can never break, because no two markers share a cell.

**Rendering** (`src/ui/widgets/map_canvas.py`)
- `MapCanvas` is a Textual **Line-API `ScrollView`**. It paints only the visible rows, so the map can be
  any size and pans with the mouse wheel (shift+wheel scrolls horizontally) and the scrollbars.
- Layers, bottom to top: owner background wash (Kestria green-black, Vosk red-black, contested amber, sea blue),
  terrain glyph colors, national borders in amber, the unit overlay, then the crosshair and cursor.
- Cursor: arrows (shift ×5), `[` / `]` to cycle units, `c` to centre, and click to select. The view follows the cursor.

**Sector Readout / Intelligence panel** (right 30%)
- Cursor on a friendly unit: exact data (strength vs establishment, morale, supply, stance, commander, grid).
- Cursor on a hostile unit: **fog of war** (below).
- Cursor on a stack: every formation in it.
- Always shown: the sector's grid ref, control, terrain modifiers, and force counts.
- The **Order of Battle** list mirrors the map. Highlighting a unit jumps the cursor to it, and vice versa.

**Fog of war on the map** (`intel.unit_report`)
- Recon accuracy = `base_accuracy` (0.45), + `frontline_bonus` (0.15) if any friendly formation is within
  `frontline_range` cells, + flag bonuses (e.g. `network_lantern` +0.2), capped at `max_accuracy`.
- Strength is shown as a **range with certainty**. Morale, supply and commander read **UNKNOWN**.
  The same misinformation rules apply as for email intel, so the range can be entirely wrong.
- **Identification**: with probability = accuracy the designation is known ("4th Rifle Division (PROBABLE)").
  Otherwise the unit is an anonymous "CONTACT H-nn", and it may be **misclassified**: the map itself
  shows the wrong symbol (e.g. a tank brigade drawn as `[H]`).
- Reports are rolled **once per unit per week** from a seed of (campaign, unit, week). Re-inspecting
  cannot average toward the truth, looking at the map never disturbs the campaign RNG, and a seeded
  campaign always produces the same reports.

**Active fog of war** `[P4]` (`src/engine/recon.py`)
- A hostile formation is drawn **only** if it lies within the detection radius of at least one
  friendly formation. Radius (`units.json → detection_radius`, in rows; columns count half): HQ 9
  (signals), artillery 7 (forward observers), infantry 5, armor 4, militia 3. At game start the
  front-line rifle divisions are visible, and the tank brigades and headquarters in depth are not.
- Each hostile formation seen at least once gets a tracking code (`CONTACT H-nn`, in order of first
  sighting, so the list does not leak how many enemies exist). When contact is lost, a dim `[?]`
  **ghost** marks the last known position for `map.ghost_weeks` (4) weeks, then the trail goes cold.
- Sector force counts, the Order of Battle list, the weekly report and the supply overlay count only
  **observed** enemies.

**Issuing orders in the War Room** `[P4]`
- Select a friendly formation (place the cursor on its symbol, or pick it in the Order of Battle),
  then press **`m`** (or `o`). The cursor turns cyan and the planned route is drawn as `·` with a
  `◇` destination. The order bar shows the target grid, ETA and path length live. **Enter** issues
  the order and **Esc** aborts.
- **`g`** opens a grid-reference prompt for typing exact coordinates (`72,15` or `072-015`). **`x`**
  cancels a standing order.
- Standing orders show as `◇` destination markers on the map. The selected unit's route is drawn,
  and the readout shows `ORDER: MOVE → 072-015 · ETA 2 WK`.

### 4.17 The Continental Map `[P7]`
`tools/generate_world_map.py` builds the 240 × 80 map (**2,400 miles** of coastline-hemmed continent) from
a coarse blueprint of 48 × 20 blocks, then roughens the coastlines with value noise (never across the
Frontier, whose trench geometry stays exact), removes stray islets and lakes, snaps settlements onto their
regions (ports onto the coast, each with a `harbour` sea cell), and routes railways and roads with A*.

- **Oceans on three sides:** the Northern Sea, the Western Ocean off Kestria's Atlantic coast, and the
  Iren Straits / Gulf of Sevrask to the south. The only way between the northern and southern seas is
  round Kestria's west coast.
- **Kestria** (west): Cassel Coast, Harrowfen, Aldmark (capital), Westmarch, Greywater, Stonereach,
  Ironvale (industrial hills) and the Eastmarch behind the front. **Vosk** (east): Vosk Marches, Karzan
  Oblast (capital), Zarnov Coast, Dornsk Basin, Sevrask Coast, Tal Varos and the Eastern Steppe.
- **50 settlements:** 2 capitals, 4 cities, 4 industrial centres (Ironvale, Kessel Works, Dornsk,
  Novo-Vosk), 10 ports (5 Kestrian, 4 Vosk, neutral Iren) and **30 towns**.
- **The Frontier** is a 40-row (400-mile) belt from the Northern Sea to the Iren Straits. Both of its
  ends are coastal, so the flanks of the trench line lie under naval guns. Kestrian trenches at x=123,
  Vosk at x=128, one rail crossing (the destroyed line at row 36) and three road crossings.
- New terrain: **hills** (movement ×0.7, defense ×1.25). Fighting within 1 row of any settlement counts
  as urban (fortification ×1.3).

### 4.18 Naval Warfare `[P7]`
Module `src/engine/naval_engine.py`, config `config.json → naval`. Fleets are real units on the map
(`domain: "sea"`): they sail only on open water, are supplied from harbours, and muster at the busiest
open harbour when raised (Military screen). Starting fleets: Kestria **KN-1** 1st Destroyer Flotilla
(Brenmouth), **KN-2** Home Fleet Battle Squadron (Port Cassel), **KN-3** 2nd Submarine Wolfpack
(Greyhaven); Vosk **VN-1** (Kolvaan), **VN-2** (Sevrask), **VN-3** (Kalinsk).

**Missions** (War Room: `t` cycles the selected squadron's mission):

| Mission | Effect |
|---------|--------|
| PATROL | Hold station; fight enemy fleets that come into contact. |
| BLOCKADE | An enemy port within **3 rows (30 mi)** is **closed** unless a ship of the port's owner is within the same range (a contested blockade fails). A closed port gives **no overseas trade** (off the weekly ledger) and **no supply** to its owner's armies or fleets. Dispatches: *NAVAL BLOCKADE: <PORT> CLOSED* / *BLOCKADE LIFTED* / *BLOCKADE ESTABLISHED*. Blockaded harbours glow red on the map. |
| BOMBARD | **Offshore bombardment:** a ship not itself in contact adds its guns' `soft_attack` × `bombard_multiplier` (a battleship: 120 per ship per week) to the nearest friendly land battle within **2.5 rows**. It consumes `naval_shells`. |

**Naval battles** (fleets in contact at sea; armies and fleets never engage each other):
- Each warship fires only while it has ammunition (**naval_shells** for guns, **torpedoes** for
  submarines, drawn from the ship's inventory, which is refilled from the national stockpile in port).
- A side's `hard_attack` × `damage_per_attack` (0.35) damages the enemy's **hull points** (`hull` per ship:
  destroyer 10, battleship 80, submarine 6), split between surface ships and submarines. Only **ASW**
  weapons (destroyer guns and depth charges) hit submarines properly; other fire does 10% of its damage
  to them. Each unit loses the same fraction of crew as of hull (÷ `hull_defense`: battleships 1.6). Ships
  sink whole, with the fractional ship rolled.
- A fleet below morale 20 **retires** 4 cells toward its harbour; a fleet with no ships left is **sunk**.
  SITREPs and After Action Reports as for land battles ("Battle of the Northern Sea").
- Rough match-ups: destroyers beat submarines, battleships beat destroyers, and submarines grind
  battleships down unless they have destroyer escort.
- **Submarines** are seen at half range by anything except destroyers.

### 4.19 Combined Arms: Artillery and Air Power `[P7]`
**Artillery** (templates with `support`: the Artillery Regiment). An artillery formation that is **not
itself in contact**, within `artillery_support_range` (**2.5 rows** = 1–2 cells behind the line) of a
friendly formation in battle, fires for that battle: its howitzers' firepower × **1.5** is added to the
side, consuming **thousands of 152mm shells a week** (54 guns × 60 rounds × stance). It takes **no
casualties** unless the enemy reaches it. Each battery supports one battle a week. SITREPs list it under
FIRE SUPPORT.

**Air power** is abstract (`src/engine/air_engine.py`, config `air`, data `data/air.json`, UI **Air Assets**
tab `6`). Air Wings (Kestria: 1st and 2nd Tactical Air Wings, 3rd Air Defence Wing; Vosk: three) are
assigned to a **sector** (a map region) or held at base:
- Effective air power = aircraft × fuel ratio × weather air factor. A patrol burns 1.5 drums of
  **aviation fuel** per aircraft a week; over a sector with a friendly battle the wing also flies ground
  support (2.5 more drums and **5 bombs** per aircraft).
- **SUPERIORITY** at ≥ 1.5 × the enemy's air power, **DENIED** at the reverse, otherwise **CONTESTED**.
  Where both sides fly, each loses aircraft = 4% of the enemy's air power (± noise).
- A land battle in a sector where a side holds superiority gives that side **+30% firepower** (+10%
  if its bombs ran out).
- Wings refill up to 6 aircraft a week from the stockpile's **strike_aircraft** (a new production line).
  Enemy air strength is shown only as an estimate.

### 4.20 Weather & Seasons `[P7]`
Module `src/engine/weather_engine.py`, data `data/weather.json`. Every week one continental condition is
rolled from the calendar month's weights (50% chance the previous week's weather simply continues).
The season follows the GameClock: Winter Dec–Feb, Spring Mar–May, Summer Jun–Aug, Autumn Sep–Nov. The
status bar shows it (`WX`), and a **METEOROLOGICAL BULLETIN** arrives each new season.

| Condition | Movement | Other effects |
|-----------|----------|---------------|
| Clear / Heat | ×1.0 / ×0.95 | — |
| Rain | ×0.85 | air ×0.6 |
| **Mud: the Rasputitsa** (mostly Mar–Apr, Oct–Nov) | **×0.45** | **armor burns 2× fuel while moving**, supply delivery ×0.8, air ×0.7 |
| **Snow & Hard Frost** | ×0.7 | freezing: frost 1.2%/wk, −3 morale, unkitted firepower ×0.75; air ×0.5 |
| **Blizzard** | ×0.5 | freezing: frost 2.5%/wk, −5 morale, unkitted firepower ×0.6; **all aircraft grounded** |

- **Winter Gear:** a formation with a **Cold-Weather Kit** for ≥ 90% of its men suffers no frostbite
  and fights at ×0.95. Kits need the new research **Cold-Weather Equipment** (10 weeks, 5,000 CR/week),
  then a production line. The Vosk start the war already kitted; Kestria does not.
- **Winter quarters:** formations holding position inside their own supply net take only 35% of the
  frost losses. Campaigning in winter without kit is ruinous.
- **Storms at sea** (chance by season: winter 45%, autumn 30%, spring 15%, summer 5%): warships not in
  port lose 3% of their strength (ships founder) and 3 morale, and sail at ×0.6.
- The campaign opens on 2 January 1984, in the dead of winter.

### 4.21 The Event Deck: Classified Dilemmas `[P7]`
Module `src/engine/dilemmas.py`, data `data/events_deck.json` (16 cards). Last in each tick, from week 3,
if no card has come up in the last 3 weeks, a card is drawn with a **16%** chance. Eligible cards are
weighted and filtered by conditions: season (the frozen convoy in winter, General Mud in the Rasputitsa,
the bumper harvest in summer and autumn), minimum week, a running research project, a battle in progress,
and story flags. Cards are not repeated unless `repeatable`.

- The card becomes `state.pending_dilemma` and pops up as a modal **CLASSIFIED DILEMMA** (Textual
  `ModalScreen`) with flavour text and 2–3 choices. Each choice shows its hint and its **visible**
  consequences (hidden effects such as Vosk tension are not shown).
- **The game loop is paused:** the TickEngine refuses to advance until the Lord Protector chooses (keys
  `1`–`3` or click). The decision is filed in the inbox ("DECISION RECORDED").
- Choices use the ordinary effect keys, plus new ones (usable by emails too): `equipment` (national
  stockpile), `army_morale` (every formation), `modifier` (a timed nation modifier, e.g.
  *factory_efficiency +15% for 4 weeks*) and `research_weeks`.
- Example: **Strike at the Ironvale Steelworks.** [1] Crush it: civil morale −6, military morale +1,
  factory output +15% for 4 weeks. [2] Concede: −60,000 CR, civil morale +5. [3] Arbitrate: −20,000 CR,
  morale +1, factory output −10% for 3 weeks.
- Debug: `python main.py --event worker_strike` forces a card at the next week; `--list-events` lists them.

### 4.22 5.56mm Re-arming `[P7]`
Templates may `modernize` kit when a tech is known. **Intermediate Cartridge** (20 weeks) re-arms
**infantry divisions**: 7.62mm battle rifles → **5.56mm assault rifles** (soft attack 0.04 vs 0.03, about
+17% divisional firepower), and the ammunition establishment becomes 5.56mm × 1.3 (lighter rounds, more
carried per man). The swap happens through the supply pipeline: 5.56mm rifles are issued from the stockpile
at the heavy-equipment rate and **old rifles are turned in to the depots one-for-one**, so a division is
never unarmed. While part of a division still carries 7.62mm, its ammunition establishment is split in
proportion, so both calibres flow down the same supply line. Militia and other formations keep 7.62mm.

### 4.23 Chain of Command & Insubordination `[P8]`
Module `src/engine/command.py`, data `data/commanders.json`. The player sits at a terminal and does not
have god-like control over the troops.
- **Commanders with hidden traits.** Every formation's commander (`Unit.commander`) carries `traits`:

| Trait | Effect |
|-------|--------|
| **Cautious** | Refuses an unsupported ASSAULT 80% of the time; +10% defense in DEFEND |
| **Aggressive** | +10% firepower in ASSAULT; refuses WITHDRAW 25% of the time |
| **Logistics-Master** | Formation uses 25% less supply; +25% resupply from the depots each week |
| **Glory-Hound** | +15% firepower in ASSAULT but takes 15% more casualties; refuses WITHDRAW 60% of the time |
| **Steady** | No quirks |

  The starting commanders' traits are fixed in the data (K-01's **Maj. Gen. Oskar Hale is Cautious**,
  K-08's Maj. Gen. Rehn is a Glory-Hound, …); everyone else, including every replacement and every newly
  raised formation, rolls one. Traits are **hidden** (`TRAITS UNKNOWN`) until they show: the first refusal,
  or the commander's first week in battle (the SITREP adds a COMMANDER ASSESSMENT).
- **Orders are acknowledged, not obeyed.** A stance change or move order is queued on the unit
  (`pending_orders`, readout: *ORDERS SENT — AWAITING ACKNOWLEDGEMENT*) and resolved by the
  **CommandSystem** at the start of the next week. An **ASSAULT without fire support** (no friendly artillery
  within 2.5 rows, no warship on BOMBARD in range, no friendly air wing over the sector) may be REFUSED:
  Cautious commanders 80%; anyone 5%, plus 25% when the army's military morale is below 30. Aggressive and
  Glory-Hound commanders may refuse to WITHDRAW.
- **A refusal** halts the order: the move order is cancelled, the formation reverts to **DEFEND**, the trait
  is revealed, and a TOP SECRET **"COMMAND INSUBORDINATION: K-01 — MAJ. GEN. OSKAR HALE"** dispatch arrives
  ("…refuses to advance without heavy fire support").
- **Relieving command** (Military screen: highlight a formation, **`f`**): a new general from the pool takes
  over with new hidden traits. It costs **−6 military morale** (the officer corps closes ranks) and **−8
  morale** in the formation. A formation that is out of contact (jammed) cannot be reached to relieve.
- Vosk formations have commanders and traits too (their combat effects apply), but they obey their own staff.

### 4.24 Electronic Warfare & Loss of Signal `[P8]`
Module `src/engine/electronic_warfare.py`, config `electronic_warfare`, AI `ai.json → vosk.ew`.
- A **jamming zone** is a circle (Vosk: 7 rows = 70 miles; natural interference: 4 rows) around a point, named
  after its sector ("the Frontier around grid 121-038"). The Vosk open one with a chance per week that grows
  with their posture (DEFEND 3%, PROBE 12%, ASSAULT 35%), centred on the **heaviest concentration of Kestrian
  land strength**, for 1–3 weeks, one zone at a time. Natural ionospheric interference (3%/week) can black out
  a smaller zone around one formation for a week.
- **Every friendly land formation inside a zone goes dark:** it drops off the Order of Battle (a *SIGNAL LOST*
  line takes its place), its map symbol becomes **`[?]` at its last reported position** (readout: *CONTACT
  LOST*, with the last report's week and grid), and the Military screen shows *NO SIGNAL*. **No orders, stances
  or command changes can reach it**; its strength, supply and morale are unknown; what it sees no longer reaches
  the staff (fog of war); its battles report *NO REPORT: SIGNAL LOST*. It keeps fighting and carrying out its
  last orders. Jamming zones show as a violet haze on the map; the sidebar counts dark formations.
- **LOSS OF SIGNAL** and **SIGNAL RESTORED** dispatches bracket each blackout. `state.signal_log` keeps each
  formation's last report.

### 4.25 Scorched Earth & Combat Engineers `[P8]`
Module `src/engine/engineering.py`, config `scorched_earth` and `engineering`.
- **Sabotage.** A formation that **routs** (60%) or falls back in **WITHDRAW** stance (30% each week it
  retreats) may blow up the railways and roads behind it: 3–6 rail/road cells within 2 rows of where it stood
  become `destroyed_rail` / `destroyed_road`, drawn as a red **`x`** on the map. **Destroyed transport gives no
  movement or supply bonus** (config: `destroyed_rail` is no longer in `map.transport_cost`), so armies and
  their supply must go cross-country. A SCORCHED EARTH dispatch reports damage on our side of the line.
- **The map remembers.** `state.map_damage` (cell → current kind wherever it differs from world.json) is part
  of the campaign state: saved, loaded, and re-applied to the map. Any change drops the cached movement/supply
  cost tables and routes.
- **Combat Engineers** (`combat_engineers`, symbol **`[E]`**, 1,200 men, 35,000 CR, 4 weeks' training; Kestria
  starts with **K-E1**, the 1st Kestrian Combat Engineer Battalion, near Kestrel Cross). An engineer battalion
  **holding position inside its supply net** rebuilds the **3 nearest wrecked sections a week within 2.5 rows**,
  including the rubble rail bed across no-man's-land. Order it onto a ruined stretch (`m` in the War Room) and a
  disaster's damage is repaired in 2–3 weeks. Its readout shows the work in reach; recruit more on the Military screen.
- Natural disasters (§4.29) wreck infrastructure the same way.

### 4.26 Victory: Enemy Capitulation `[P8]`
Checked every week after the loss conditions (`fail_states.check_victory`, config `victory`):
- **Occupation:** Kestrian land troops (not routing) within 1 row of **Karzan**, the Vosk capital on the far
  side of the map, with no Vosk land formation as close, for **2 consecutive weeks**. The first week brings a
  FLASH dispatch ("KESTRIAN TROOPS IN KARZAN"); an enemy counter-attack into the city resets the count.
- **Collapse:** the Vosk treasury is at or below 0 **and** Vosk military morale has fallen to 0. Blockades
  starve their trade; since Phase 8 **every** army's military morale moves with its battles (+3 victory,
  −4 defeat), and bankruptcy drains it further.
- Either way: a full-screen **"VICTORY: ENEMY CAPITULATION"** modal (amber, *OPERATION CONCLUDED — THE WAR IS
  WON*) with the terms of surrender and campaign statistics. The campaign ends; restart or exit.

### 4.27 Save / Load `[P8]`
Module `src/engine/savegame.py`.
- **`CTRL+S`** (or `F5`) anywhere on the desktop saves the whole campaign to `savegame.json` in the game folder
  (written atomically). **`python main.py --load`** resumes it; `--load other.json` names a file.
- Saved: every piece of dynamic state (clock, nations, formations and inventories, orders, commanders and hidden
  traits, **epidemics (every infected site and its quarantine), relief duty, emergency cards on screen or queued**, the inbox with read/reply state, scheduled follow-ups, flags, the campaign RNG's exact state, the AI's
  hidden posture and tension, contacts, engagements, battles, training, air wings, weather, blockades, jamming,
  map damage, pending dilemmas, the victory counter). Rebuilt from `data/`: config, catalog, email templates, the
  world map (damage re-applied) and derived caches. A loaded campaign continues **exactly** as the original
  would have — the tests check that two copies stay identical for weeks after a save.
- Format: JSON with small tags for tuples, sets, non-string dict keys, dates and model classes; `version` 1.

### 4.28 The Home Front: Epidemics `[P8]`
Module `src/engine/crisis_engine.py`, data `data/crises.json`. A nation does not stop suffering because it is at
war. From week 4, each week has a **6%** chance of a new outbreak (3% once Field Medicine is known):

| Disease | Breaks out in | Per infected settlement / week | Per infected formation / week | Spreads (chance, reach) |
|---------|---------------|-------------------------------|-------------------------------|------------------------|
| **Trench Typhus** | a front-line formation | civil morale −1, output −3% | **3% of its men die**, morale −3 | 35%, 2.5 rows from troops |
| **Industrial Influenza** | an industrial centre | civil morale −1.5, output **−8%** | 1.2%, morale −2 | 30%, 14 rows from towns |
| **Cholera** | a city, port or capital | civil morale **−2**, output −5% | 2.5%, morale −3 | 25%, 12 rows from towns |

- Output losses double in industrial centres and are capped at −50% (they apply to every production line through
  `production.forecast`). Sites: `state.infections["city:<name>" | "unit:<id>"]`, shown as **`[INFECTED]`** in the
  Order of Battle, the unit and sector readouts, the Military screen, and as violet settlements on the map. The
  sidebar counts epidemics and sites; the weekly report lists them.
- **Spread:** each week every unchecked site may infect its nearest clean neighbour — the next town, or the
  formations marching through. Infections burn out on their own only slowly (4–6% a week).
- **The cure** is a choice, not a timer. A new outbreak raises a **CRITICAL EMERGENCY** modal (red, the game is
  paused until you decide), and it comes back every 3 weeks while the outbreak keeps spreading unchecked:
  1. **Fund Quarantine Protocols:** 30,000 + 8,000 CR per infected site. Every site stops spreading and is cleared within **2 weeks**.
  2. **Military Cordon:** civil morale −4, military morale −2. The spread stops, but nobody is cured.
  3. **Leave It to the Doctors:** civil morale −2. It may burn out; it may not.
- **Field Medicine** (Research tab, 12 weeks, 6,000 CR/week) clears every infection within 2 weeks, forever after,
  and halves the chance of new outbreaks.
- Survey (10 campaigns of 40 weeks, choices made at random): 0–3 outbreaks and 0–4 disasters per campaign. Two
  campaigns ended in revolution, one after an outbreak left unchecked reached 10 sites.

### 4.29 The Home Front: Natural Disasters `[P8]`
From week 4, each week has a **5%** chance that a disaster strikes a Kestrian region of matching terrain:

| Disaster | Strikes | Damage |
|----------|---------|--------|
| **Severe Flooding** | river, coastal and marsh regions | 5–9 sections of rail/road wrecked, ~6,000 dead, civil morale −2 |
| **Earthquake** | mountains and hills | 6–10 sections wrecked, ~9,000 dead, civil morale −3 |
| **Mine Collapse** | hills (the Ironvale coalfield) | 3–6 sections wrecked, ~1,500 dead, factory output −10% for 4 weeks |

- The wrecked cells (`[=]` becomes `x`) give no movement or supply bonus **at once**, so the supply lines through the region
  are cut until Combat Engineers rebuild them (§4.25).
- An immediate **CRITICAL EMERGENCY** modal forces the hard choice:
  1. **Fund Relief:** −50,000 CR, civil morale +2.
  2. **Deploy the Military:** the nearest formation not in battle (named on the button) is pulled off the line for
     relief work. For **3 weeks** it cannot move, change stance or fight (firepower ×0.2 if attacked). Civil morale +4.
  3. **Ignore:** civil morale **−8**, a long step toward Protocol Zero.
- Two emergencies in one week queue up: the second modal appears as soon as the first is answered.
- Debug: `python main.py --crisis outbreak` (or `disaster`) forces one at the first week.

---

## Expansion 1.1 — The Living World `[X1.1]`

**Goal:** turn the war simulator into a geopolitical grand-strategy game. The world beyond the map is larger (three
off-map powers who trade, sell arms and choose sides), the nation deeper (every city a living place with people, morale,
construction and its own newspaper), and the enemy has a voice (the Hotline). Faction sheets, Hotline cables and many
headlines come from the design team's content sheets (`data/diplomacy.json`, `data/hotline.json`, `data/cities.json`).

### X1. Foreign Affairs & Lend-Lease (Diplomacy tab `7`)
Module `src/engine/diplomacy.py`, data `data/diplomacy.json`.

| Power | Character | Start | Trade (CR/wk) | Sells |
|-------|-----------|-------|---------------|-------|
| **The Oakhaven Republic** | Naval superpower across the western ocean; prefers democracies, **disgusted by high taxes** (−0.5/wk at High, −1.5/wk at Oppressive). Never leans past −5 toward the Vosk. | +15 (treaty with Kestria) | 3,500 | medical supplies, strike fighters (48), **light tanks (12)**, howitzers, trucks |
| **United Provinces of Tor** | Industrial autocracy to the south; ignores suffering, **respects strength** (battle swings ×3). | −5 | 2,000 | rifles (10,000), 152mm shells, fuel |
| **Sovereign State of Vael** | Neutral scientific hub; **sells no weapons of war**; ignores battles; bounded ±50. | +25 | 1,500 | winter gear, aviation fuel, medical supplies |

- **Alignment:** one score per power, **−100 (with the Vosk) … +100 (with Kestria)**. It drifts 1/week back toward its
  starting value.
- **Envoys** (`g`, 25,000 CR): swing a power up to +8 toward the sender (less the further it already leans).
- **Trade agreements** (`t`): sign when a power leans +10 your way. They add weekly income while one of your trade ports
  is open; **with every port blockaded the trade is suspended**. The power cancels when it drifts back past 0.
- **Lend-Lease** (`b`/Enter on a package): pay now, and the convoy sails 1–5 weeks to the first open port (Port Cassel,
  Saltmere, Greyhaven, Northwatch, Brenmouth) and unloads into the national stockpile.
  - While every port is blockaded the convoy waits at sea.
  - Each week at sea, every enemy submarine wolfpack out of port has an 8% chance to torpedo 30% of the cargo, and the
    neutral blames the attacker (−5).
  - **Keeping ports open and hunting wolfpacks with destroyers matters.**
- **The Vosk foreign ministry** does all of this from the other end of the scale: it courts the powers, signs trade,
  and buys arms landed in Vosk ports, which Kestrian blockades can hold at sea. A passive player watches the neutrals
  drift toward the Hegemony.
- **Won battles** swing every power (except Vael) toward the winner.
- **Medical supplies** (new stockpile item; bought abroad or produced after Field Medicine): 200 crates per infected
  site a week halve the epidemic toll and double the burnout chance.

### X2. Deep City Management (Cities tab `8`)
Modules `src/engine/cities.py`, model `src/models/city.py` (`City`), data `data/cities.json`.
- **Every Kestrian settlement is a City** (26 of them): `population` (by type: capital ~900k, city ~320k, industry
  ~260k, port ~180k, town ~45k), `local_morale`, `buildings`, a `construction_queue` and a local news wire.
- **Local morale** moves 20% a week toward national civil morale plus local factors:

| Factor | Effect on local morale |
|--------|------------------------|
| Epidemic in the city | −25 (−10 if quarantined) |
| Battle within 40 miles | −15 |
| Enemy in sight | −6 |
| Harbour blockaded | −10 |
| Wrecked infrastructure nearby | −12 |
| Hospital / bunkers / new industry | +8 / +4 / +3 |

  A city below 15 is in **unrest** and costs the nation 0.25 civil morale a week.
- **Construction** (City Inspector: `h`, `b`, `i`; `x` cancels the last project for a 50% refund). Paid up front,
  one project at a time per city:

| Building | Cost | Weeks | Effect |
|----------|------|-------|--------|
| **Hospital** | 60,000 | 6 | Epidemics start there and reach it only **one fifth** as often; local morale +8 |
| **Bunker Complex** | 45,000 | 4 | Kestrian formations within 1 row defend as if **entrenched** (fortification ×1.8) |
| **Local Industry** (max 2) | 120,000 | 10 | **+1 military factory** for the national war economy |

### X3. The Local News Wire
Each week a city may print a headline (always, when something urgent is happening), chosen from the pool of its most
pressing situation: epidemic, quarantine, battle nearby, enemy near, blockade, disaster damage, construction, a
completed building, winter, unrest, high spirits, or a quiet week. Examples: *"Citizens report hearing artillery
throughout the night; panic buying at local markets."* · *"Mass graves dug outside city limits as quarantine fails."* ·
*"Steelworker quotas exceeded; patriotic parades in the town square."* The last 12 are kept per city (saved with the game).

### X4. The Vosk Hotline & the expanded event deck
Module `src/engine/hotline.py`, cables `data/hotline.json` (signed **Chancellor V. Krov**), config `hotline`. At most one
call every 6 weeks, answered like any dispatch (a/b/c), with a 2-week deadline:

| Trigger | Cable | Replies |
|---------|-------|---------|
| Vosk military morale < 20 | REQUEST FOR ARMISTICE | Accept: **the war ends in a negotiated victory** · Refuse |
| The Vosk lose a battle of ≥ 2,500 men | URGENT CEASEFIRE PROPOSAL | Accept a 4-week ceasefire (their AI holds its fire; tension −20, civil +4, military −3) · Refuse |
| The Vosk win such a battle, or stand on Kestrian soil | TERMS OF SURRENDER | Reject · Buy a 4-week pause (120,000 CR) |
| The Vosk go over to the ASSAULT | UNCONDITIONAL WARNING | Pay an indemnity · **Withdraw our divisions from the Frontier** · Defy |

- **Breaking a ceasefire** (a new battle during it) jumps tension by 20 and costs 10 alignment with every power.
- Military morale now swings with battles **in proportion to their size** (full swing at 4,000 casualties). The Vosk
  regime slowly restores its army's morale (+0.4/week up to 70) unless bankrupt, so collapse has to be earned.
- **Event deck:** six new cards hook into the powers and cities:
  - *Oakhaven Wants a Consideration* (a bribe; needs the Oakhaven treaty)
  - *Aldmark Workers Strike for a Hospital* (build it, promise it, or break the strike)
  - *Tor Weighs an Embargo*
  - *The Oakhaven Volunteer Squadron* (alignment ≥ +50)
  - *A Vaelish Medical Mission* (a hospital for Greywater)
  - *Arms for the Enemy* (seize Toran freighters)

  New card conditions: `requires_relation`, `requires_trade` and `requires_city_without`. New effect keys: `relations`,
  `rival_relations`, `build`, `city_morale`, `ceasefire`, `armistice` and `withdraw_frontier`.

### X5. Save / load
Cities (`City` objects), `foreign` alignments and treaties, `shipments` at sea, the ceasefire and Hotline timers are all
part of the saved GameState; the round-trip test plays a saved and a loaded campaign side by side and checks they stay
identical.

---

## 5. Architecture

### 5.1 Principles
- **Strict separation of logic and UI.** Engines never import from `src/ui`. The UI calls engine
  entry points (`TickEngine.advance()`, `event_manager.respond()`, `mark_read()`) and then
  `app.state_changed()`.
- **Reactive UI.** `CommandTerminalApp.revision` is a Textual `reactive`. Every state-displaying
  widget watches it and redraws itself, so there is no manual refresh plumbing. `StatusBar` mirrors
  each headline stat into its own reactive `var`, so only changed cells redraw, and each change
  flashes its delta (▲/▼) for 2.5 seconds.
- **Models are dumb data** with trivial invariants (clamping, validation). No simulation math.
- **All tunable numbers and content live in `data/*.json`.**

### 5.2 Directory layout

```
command-terminal/
├── main.py                  # python main.py [--skip-boot] [--seed N] [--reveal] [--event CARD] [--list-events] [--load [FILE]]
├── requirements.txt / requirements-dev.txt (pytest)
├── GAME_DESIGN.md
├── data/
│   ├── config.json          # dates, economy coefficients, fail-state thresholds, intel, seed
│   ├── nations.json         # starting stats, factories, production lines, national stockpile, resource output
│   ├── orbat.json           # starting formations with exact map locations
│   ├── ai.json              # AI Director postures, thresholds, flanks, SIGINT
│   ├── resources.json, units.json   # units: symbol, move_speed, detection_radius, loadout
│   ├── equipment.json       # every producible/carried item: production cost & rate, combat stats
│   ├── tech_tree.json       # R&D tree (placeholder); known_at_start gates production
│   ├── events/emails.json   # email templates + event chains
│   ├── events/system_alerts.json  # SYSTEM PURGE texts per loss cause
│   ├── events/generated.json      # SIGINT, BORDER CLASH, ISOLATED dispatch templates
│   ├── map/world.json       # War Room grid (240×80): base art, region_rows, sea zones, features, harbours, transport
│   ├── weather.json         # seasons, conditions, monthly weights, storms
│   ├── air.json             # air wings
│   ├── events_deck.json     # CLASSIFIED DILEMMA cards
│   ├── commanders.json      # commander traits, starting assignments, replacement pool, cost of relieving
│   ├── crises.json          # epidemics (diseases, spread, quarantine) and natural disasters
│   ├── diplomacy.json       # off-map powers: alignment, trade, lend-lease (X1.1)
│   ├── cities.json          # city populations, buildings, local morale, news headlines (X1.1)
│   ├── hotline.json         # Chancellor V. Krov's cables (X1.1)
│   └── ui/boot_sequence.json
├── src/
│   ├── models/              # nation.py, inbox.py, game_state.py, military.py (Unit, MoveOrder, Contact), battle.py,
│   │                        # world_map.py (WorldMap), ai.py (AIState)
│   ├── engine/
│   │   ├── data_loader.py   # new_game(), content validation
│   │   ├── effects.py       # effect interpreter
│   │   ├── intel.py         # fog-of-war estimates (emails + weekly enemy-unit reports)
│   │   ├── map_overlay.py   # visible markers, stacking, ghosts, per-cell lookups
│   │   ├── movement.py      # A* routes, orders, simultaneous movement, skirmish detection
│   │   ├── logistics_engine.py  # supply tracing, ZOC, attrition, fuel burn, resupply, replacements
│   │   ├── production.py    # military factories, production lines, national stockpile, AI allocation
│   │   ├── recruitment.py   # raising and training new formations, mustering, AI replacements
│   │   ├── research.py      # R&D projects, breakthroughs, unlocks, tech effects
│   │   ├── combat_engine.py # battles, firepower from inventory, stances, routs, artillery support, SITREP / AAR
│   │   ├── naval_engine.py  # missions, blockades, naval battles, bombardment, storms, Vosk admiralty
│   │   ├── air_engine.py    # air wings, sectors, superiority, fuel and bombs
│   │   ├── weather_engine.py# seasons, weekly weather, mud, frostbite
│   │   ├── dilemmas.py      # the event deck: draw, pause, resolve
│   │   ├── command.py       # commanders, traits, insubordination, relieving command
│   │   ├── electronic_warfare.py  # jamming zones, loss of signal
│   │   ├── engineering.py   # scorched earth sabotage, combat engineer repairs, map damage
│   │   ├── savegame.py      # save / load (JSON codec for the whole GameState)
│   │   ├── crisis_engine.py # the home front: epidemics, natural disasters, relief duty, emergency cards
│   │   ├── diplomacy.py     # foreign powers, envoys, trade agreements, lend-lease convoys, Vosk foreign ministry
│   │   ├── cities.py        # city management: local morale, construction, bunkers, the local news wire
│   │   ├── hotline.py       # the Vosk Hotline: ceasefire, surrender terms, ultimatum, armistice
│   │   ├── recon.py         # detection radius, contacts, ghosts (active fog of war)
│   │   ├── ai_director.py   # Vosk DEFEND / PROBE / ASSAULT state machine
│   │   ├── sigint.py        # intercepts of major AI orders
│   │   ├── event_manager.py # delivery, respond(), deadlines, follow-ups
│   │   ├── economy_engine.py# placeholder ledger
│   │   ├── reports.py       # weekly status report
│   │   ├── fail_states.py   # revolution / coup / collapse, and VICTORY (capitulation)
│   │   ├── tick_engine.py, systems.py, text.py
│   │   └── (combat_engine.py is listed above)
│   └── ui/
│       ├── app.py           # owns GameState + TickEngine, `revision` reactive
│       ├── screens/         # boot, terminal, confirm (reply), coordinates, game_over (purge lock), dilemma (event card)
│       ├── widgets/         # status_bar.py (reactive), sidebar.py (nav + ADVANCE WEEK), map_canvas.py (War Room)
│       └── views/           # inbox, economy, military, map (War Room), research, air, diplomacy, cities
├── tools/generate_world_map.py  # optional: regenerate world.json (overwrites it)
└── tests/                   # conftest (calm-world fixture), test_engine, test_ui, test_war_room, test_phase4–8
```

### 5.3 Tick order
0. Refused while a CLASSIFIED DILEMMA is pending (`DilemmaPendingError`); then the clock advances
1. **Weather**: this week's condition and storms; a bulletin at the change of season
1a. **Command**: commanders acknowledge last week's orders — or refuse them (COMMAND INSUBORDINATION)
2. **AI Director**: posture, tension, land orders and attack stances (+ SIGINT), fleet orders and missions, jamming
3. **Movement**: armies march and fleets sail simultaneously; border clashes / naval contacts → ENGAGED + dispatch
4. **Air**: wings contest their sectors (AI tasks its wings first); superiority, losses, fuel and bombs
5. **Combat**: routed units rally; land battles (with artillery, naval gunfire, air support, winter penalty, commander traits) and naval battles; routing/withdrawing formations may sabotage rail and road; SITREP / AAR
6. **Naval**: storms at sea; blockades imposed and lifted (+ dispatches)
6a. **Hotline**: ceasefires tick or break; the Vosk Chancellor may call
7. **Recon**: detection (submarines at half range), contacts acquired/lost, ghosts
8. **Research**: labs progress (unless bankrupt); breakthroughs unlock lines and upgrade establishments
9. **Recruitment**: training advances; formations muster at the capital, warships at a harbour
10. **Production**: domestic resource output; factories produce into the national stockpile (AI rebalances first)
11. **Logistics**: land and sea supply nets, fuel burn (mud ×2 for armor), supply, attrition, resupply, re-arming turn-in, replacements
11a. **Engineering**: combat engineers rebuild wrecked track
12. **Frost**: frostbite for formations without winter kit
12b. **Cities**: local morale, construction, news; **Diplomacy**: alignment drift, treaties, the Vosk foreign ministry, lend-lease convoys
12a. **Crises**: relief duty counts down; epidemics take their toll, clear, spread (and nag); new outbreaks and disasters raise CRITICAL EMERGENCY modals
13. **Economy**: ledger for every nation (taxes, trade, overseas trade minus blockaded ports, factories, research), timed modifiers expire
14. **Events**: expire overdue dispatches (apply `on_expire`), deliver due emails (apply `on_arrival`)
14a. **Electronic warfare**: jamming zones tick down (SIGNAL RESTORED), natural interference, last reports logged
15. **Status report**: Weekly Status & Financial Report (now with weather and blockaded ports)
16. **Fail states & victory**: revolution / coup / collapse → SYSTEM PURGE + Protocol Zero lock; enemy capitulation → VICTORY modal
17. **Dilemmas**: maybe draw a CLASSIFIED DILEMMA card (pauses the game until answered)

### 5.4 Controls
| Key | Action |
|-----|--------|
| `1`–`8` | Inbox / Economy / Military / Map / Research / Air Assets / Diplomacy / Cities |
| `↑ ↓ Enter`, `Tab` | Navigate lists / move focus |
| `a`–`d` | Reply to the open dispatch with option A–D (asks to confirm: `y` / `n`) |
| `h` | Hide / show archived dispatches |
| Map: arrows / shift+arrows | Move the cursor 1 / 5 cells (the view follows) |
| Map: `[` `]` | Previous / next unit |
| Map: `c`, click, wheel | Centre on the cursor, select a cell, pan |
| Map: `m` / `o` → cursor → Enter | Move order for the selected friendly formation (Esc aborts) |
| Map: `g` | Type an exact grid reference for the order |
| Map: `x` | Cancel the selected formation's standing order |
| Map: `s` | Toggle the supply overlay |
| Map: `t` | Cycle the selected formation's stance: DEFEND → ASSAULT → WITHDRAW; for a warship, its mission: PATROL → BLOCKADE → BOMBARD |
| Economy: `+` / `-` / `0` | Assign / remove a factory on the highlighted production line / close the line |
| Economy: `[` / `]` | Lower / raise the tax policy (Low · Normal · High · Oppressive) |
| Military: `r` / `x` | Raise the highlighted formation type / cancel the highlighted formation in training |
| Research: `Enter` or `r` / `x` | Start (or switch to) the highlighted project / stop research |
| Air Assets: `[` / `]` / `0` | Move the highlighted wing to the previous / next sector / recall it to base |
| Dilemma: `1`–`3` or click | Choose (the week cannot advance until you do) |
| Military: `f` | Relieve the highlighted formation's commander (−6 military morale) |
| `ctrl+s` / `F5` | Save the campaign (`python main.py --load` resumes it) |
| Diplomacy: `g` / `t` / `b` | Send an envoy / sign or cancel a trade agreement / buy the highlighted lend-lease package |
| Cities: `h` / `b` / `i` / `x` | Build a Hospital / Bunker Complex / Local Industry in the highlighted city / cancel the last project |
| Emergency: `1`–`3` or click | Answer a CRITICAL EMERGENCY (outbreak or disaster); the game waits for you |
| Game over: `r` / `q` | Restart the campaign / exit |
| `n` or **▶ ADVANCE WEEK** | Advance one week |
| `q` | Log out |

### 5.5 Testing
`python -m pytest` (about 200 tests, 3–7 min) runs engine tests, fuzzed campaigns (random replies and random
move orders, with invariants checked every week) and headless Textual tests that drive the real UI:
replies, advance week, the War Room overlay at several terminal sizes, issuing and cancelling orders,
a live seeded campaign until SIGINT and a border clash arrive, factory assignment on the Economy
screen, the stance key, combat draining ammunition, empty armor unable to fight, routs, destruction,
withdrawals, weekly SITREPs and AARs, plus a fuzzed full war (random orders and stances) whose
invariants (no negative stocks, no dangling engagements, routing units out of contact) are checked
every week. `tests/conftest.py` holds the weather clear and the event deck shut for tests written before
Phase 7, unless a test is marked `live` (the fuzzed campaigns and Phase 7 tests run the full world, answering
any dilemma that comes up). Phase 7 tests cover the map and scale, march speeds, sea-only movement, blockades
cutting trade and supply, naval battles sinking ships with shells and torpedoes, bombardment and artillery
support against controls, air superiority, mud, frostbite and storms, the dilemma pause (engine and modal),
5.56mm re-arming, and naval recruitment. Set `CT_SCREENSHOTS=<dir>` to save
SVG screenshots from the UI tests.

---

## 6. Roadmap

| Phase | Scope |
|-------|-------|
| **1** | Foundation: structure, models, data, TUI shell, boot screen, turn advance, email delivery. ✅ |
| **2** | Inbox loop: replies with effects, confirmation, event chains, deadlines, weekly report, placeholder ledger, fail states, fog-of-war estimates, reactive TUI. ✅ |
| **3** | **War Room**: X/Y grid map, NATO-style symbology, overlay and stacking, pannable Line-API map, sector/intel readout, fog of war on units, and misidentification. ✅ |
| **4** | **Living front**: move orders and A* over a road/rail/trench layer, simultaneous movement, skirmish detection, supply-line logistics (ZOC, isolation, attrition), the Vosk AI Director (DEFEND/PROBE/ASSAULT), active fog of war, SIGINT intercepts. ✅ |
| **5** | **Meatgrinder**: military factories and production lines, national stockpile, physical resupply pipeline, combat driven by inventory (ammo and fuel), stances, trench fortification, routs and destruction, SITREP / After Action Reports. ✅ |
| **6** | **The backbone of the state**: tax policies and trade income, bankruptcy spiral, recruitment and training (muster under-equipped), R&D with breakthroughs, unlocks, establishment upgrades and tech effects, Protocol Zero lock with restart. ✅ |
| **7** | **Continental war**: 240×80 map at 10 miles per cell with oceans, 50 settlements and ports; miles-per-day marching; navies (destroyers, battleships, submarines) with PATROL / BLOCKADE / BOMBARD, naval battles and blockades of trade and supply; artillery fire support; abstract air wings and air superiority; weather and seasons (Rasputitsa, frostbite, Cold-Weather Kits, storms); the CLASSIFIED DILEMMA event deck; 5.56mm re-arming. ✅ |
| **8** | **The home front, human friction and the endgame**: epidemics (Trench Typhus, Industrial Influenza, Cholera) with spread, quarantine, cordons and Field Medicine; natural disasters wrecking infrastructure with CRITICAL EMERGENCY choices (fund relief, deploy the military, ignore); commanders with hidden traits and insubordination, relieving command; electronic warfare and loss of signal; scorched earth and combat engineers; victory by capitulation (occupy Karzan, or bankrupt and demoralise the Hegemony); save/load. ✅ |
| **X1.1** | **The living world**: three off-map powers (Oakhaven, Tor, Vael) with one alignment axis, envoys, trade agreements, lend-lease convoys through open ports and past wolfpacks, the Vosk foreign ministry; 26 living cities with population, local morale, hospitals, bunkers and new industry; the local news wire; the Vosk Hotline (ceasefire, surrender terms, ultimatum, armistice); six new event cards; medical supplies. ✅ |
| 9 | Strategic bombing of factories (air wings over industrial centres), amphibious landings, multiple research slots. |
| 10 | Domestic Politics & the Draft (§7.4): war weariness, rationing, conscription laws, factions. |
| 11 | Diplomacy, balance pass. |

---

## 7. Future Phases — Hardcore Simulator Mechanics `[PLANNED]`

The long-term goal: nothing on the battlefield is abstract. Every rifle, shell and tank is
researched, built in a real factory, shipped along a real supply line, and physically carried by
a formation, and the home front pays for all of it.

### 7.1 Hearts of Iron-style R&D `[core built in P6]`
- **Granular technologies**, not "+10% attack" cards: 7.62mm battle rifle → **5.56mm intermediate
  cartridge**, **Kevlar body armor**, light → **Medium Tank Chassis**, **APFSDS** penetrators,
  **night vision** (image intensifiers), assembly-line retooling, doctrines.
- **Research slots** (limited by universities, budget and scientists), weekly treasury cost per
  active project, prerequisites, and a **branch tree**. Data: `data/tech_tree.json` (placeholder,
  already loaded into `catalog["tech_tree"]`).
- Techs **unlock equipment**; they do not buff stats directly. An army only benefits once factories
  build the new kit and it reaches the front.
- Trade-offs are real: switching to 5.56mm means two incompatible ammunition types in the supply
  chain until the old rifles are retired.
- Intelligence can steal or estimate Vosk research (another fog-of-war surface).

### 7.2 Military-Industrial Complex `[core built in P5]`
- **Factories** (civilian and military) sit in provinces on the map and can be bombed, captured
  or cut off by the logistics network.
- Each military factory is **assigned a production line** for a specific item from
  `data/equipment.json` (e.g. `shell_152_he`, `mbt_medium`). Output depends on factory efficiency
  (it grows the longer a line runs; retooling resets it), raw inputs (steel, fuel, munitions from the
  resource chain), and labour drawn from the manpower pool.
- Output goes into **national stockpiles** of physical items, then travels **along the supply network**
  to depots and formations. Generic "supply %" is replaced by the actual items a unit needs.
- Shortages propagate: no coal → no steel → no shells → artillery falls silent.

### 7.3 Unit Equipment Loadouts `[core built in P5]`
- `Unit.equipment_inventory` (implemented as a placeholder in Phase 4) holds the physical
  inventory: rifles, rounds, tanks, shells, trucks, rations, and so on. Each template's `loadout`
  (`units.json`) is its establishment. Current inventories are seeded from it, scaled by strength.
- **Combat stats will derive entirely from inventory.** An armored brigade without 120mm shells
  cannot fight; infantry without rifles has no soft attack; trucks set supply throughput and speed;
  Kevlar reduces casualties; night vision removes night penalties.
- Casualties consume equipment as well as men. Captured depots and destroyed units yield (or lose)
  equipment.
- The readout will show a **loadout sheet**: have vs. establishment, with shortages in red.

### 7.4 Domestic Politics & the Draft
- **War weariness** accumulates from casualties, rationing and length of war, and drags on civil
  morale, productivity and military morale.
- **Civilian rationing** (none / partial / total) trades civil morale for grain, fuel and steel
  freed for the war effort.
- **Conscription laws** (volunteer → limited draft → general mobilization → total war) set the
  manpower pool and training time, with harsh morale and economic penalties at the top end. They
  pass through the inbox as legislation dispatches.
- **Factions** (military, industrialists, labour, clergy, the press) react to policy and events, and
  can back or topple the government. This ties into the existing revolution / coup / collapse fail
  states.

---

## 8. Open Questions
- Real-time-with-pause, or strictly turn-based? (Currently strictly turn-based.)
- Should a SYSTEM PURGE offer "start new campaign" in-app, or only by relaunching?
- Balance: the scripted event deck runs out after about 8 weeks. The AI and SIGINT now generate steady pressure,
  but the economy is still a placeholder. Recurring and triggered events should add more.
- Casualty and consumption rates are first-pass numbers (see the balance note in §4.10). Tune after real play.
- Overproduction piles up in depots (rifle and ammunition stockpiles grow fast). Should stockpiles cost upkeep, or spoil?
- Civil morale drifts down over a campaign even at Normal taxes (about 62 → 39 in 40 weeks, from expired dispatches
  and bankruptcy-free wear). Is that the right baseline pressure, or should peace and victories restore it faster?
- Civil morale drift is gentler since the event deck (median 54 after 40 weeks). **Decided (Phase 8): keep it** —
  the deck provides the crises.
- Frostbite in the first winter costs Kestria 450–3,800 men in the survey (most formations sit in winter quarters).
  Harsher? Should formations in trenches count as winter quarters?
- Should air wings bomb factories and rail (strategic bombing) in Phase 8, or stay purely tactical?
- Warships cross the map in 2–3 weeks. **Decided (Phase 8): keep** — the contrast with trench warfare is the point.
- Should Vosk armies also suffer insubordination (their commanders refusing the AI), or is friction the player's burden?
- Jamming is frequent in PROBE/ASSAULT (roughly every 5–8 weeks in a survey). Tune once real campaigns are played.
- Can Kestria jam back (its own EW brigade blinding the Vosk AI's knowledge)? Currently the AI has perfect information.
- Should the Vosk also suffer epidemics and disasters (a plague behind their lines as an opportunity)? Currently only
  the home front suffers them.
- Should the player be able to make peace with Tor or Oakhaven formally (alliances that bring in volunteers or fleets)?
- Cities under enemy occupation: should Vosk-held Kestrian cities keep their data (and rise up behind the lines)?
- Crises are harsh when ignored: two in ten random-choice campaigns ended in revolution. Is that the right pressure?
- Should the player see a supply-flow projection (who will be OVEREXTENDED if a move order completes) before
  confirming an order?
