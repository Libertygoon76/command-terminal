# COMMAND TERMINAL — Game Design Document

> Living document. Update it whenever a system is designed, changed, or cut.
> Last major revision: Phase 4 (movement, logistics, AI Director, SIGINT, border clashes), 2026-09-24.

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
| **Win / loss** | **Open-ended survival.** No win screen. Three brutal loss conditions, below. |
| **Fog of war** | **Yes, always.** Intelligence arrives as **ranges with a stated certainty %**, and reports can be **entirely wrong** while still claiming confidence. |

### Loss conditions (checked at the end of every turn)
| Cause | Trigger (`data/config.json → fail_states`) |
|-------|----------------------------------------------|
| **Armed revolution** | Civil morale ≤ `revolution_morale` (0) |
| **Military coup** | Military morale ≤ `coup_military_morale` (10) |
| **State collapse** | Treasury negative for `bankruptcy_grace_weeks` (8) consecutive weeks, **or** treasury ≤ `collapse_debt_limit` (−1,500,000) |

On failure the engine pushes a pinned, undeletable **"CRITICAL ALERT: SYSTEM PURGE"** dispatch
(`data/events/system_alerts.json`), and the terminal locks: no replies, no advancing the week.

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
- After a fall, `advance()` raises `GameOverError`.

### 4.2 Nation Management `[P2 partial]`
- **Treasury**: can go negative. Debt pays 1%/week interest, and while it is negative the army is
  unpaid (−3 military morale per week).
- **Tax rate**: drives revenue. Above 25% it erodes civil morale every week.
- **Population**, **Manpower** (the recruitable pool).
- **Civil morale** 0–100, with bands COLLAPSING (<20), UNREST (<40), STEADY (<70), HIGH.
  It sets productivity (75% at 0 → 100% at 100), and at 0 there is a revolution.
- **Military morale** 0–100: loyalty of the armed forces. At 10 or below there is a coup.
- `[PLANNED]` Construction queues, edicts, stability, corruption, war support, factions.

### 4.3 Economy `[P2 placeholder]`
- Weekly ledger (`economy_engine.compute_ledger`):
  `tax = population × tax_rate × tax_revenue_per_capita_weekly × productivity(morale)`,
  minus civil administration, armed forces pay, and debt interest. All values are in config `economy`.
- `[PLANNED]` Resources, production chains, stockpile consumption, markets, and trade routes
  (the data already exists in `resources.json`).

### 4.4 Movement & Orders `[P4]`
Module: `src/engine/movement.py`.
- **Orders.** Each `Unit` has an `active_order` (`MoveOrder(target, issued_turn)`) or `None`,
  a `status` (HOLDING / MOVING / ENGAGED), and `move_points` carried over between weeks.
  `issue_move_order()` validates ownership, bounds, sea and reachability. The player and the AI use
  the same function.
- **Speed.** `move_speed` in `units.json` is movement points per week on open ground: infantry 4,
  armor 8, artillery 3, militia 3, HQ 5. Low supply (<25%) halves it.
- **Pathfinding.** A* over 4-neighbour cells, re-planned every week. Step cost:
  - along **rail / road / destroyed rail** (`world.json → transport`), a flat
    `map.transport_cost` (0.35 / 0.55 / 0.8) that ignores terrain;
  - otherwise `1 / terrain movement` (mountains ×2.5, marsh/mud ×2, urban ×1.4, river ×1.25);
  - **trench lines** cost an extra ×1.8 (`map.obstacle_cost`);
  - a column is half a row (`map.column_scale` 0.5): 1 row ≈ 10 km, 1 column ≈ 5 km.
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
- **Sources:** friendly cities, capitals and ports in regions the nation owns, plus its **home map edge**
  (Kestria: the western coast; Vosk: the eastern hinterland).
- **Tracing:** a multi-source Dijkstra over the same costs as movement, so **roads and rail carry
  supply far** and mountains and marsh strangle it. An in-supply **HQ / Logistics** formation is a
  forward depot, extending the net by `hq_range`.
- **Enemy territory:** supply cannot use an enemy's roads or rail, and pays terrain cost ×
  `hostile_territory_factor` (1.5). Armies can race down enemy roads, but their supply cannot follow.
- **Zones of control:** every cell within `zoc_radius` of an enemy formation. A supply path may end
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
| any | — | → DEFEND when Kestrian strength past x=72 reaches 35% of Vosk front strength |

- **Logistics-aware:** each posture first pulls starving, out-of-supply formations back to the
  nearest cell of its own supply network.
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

### 4.9 Large-Scale Combat `[PLANNED — Phase 5]`
- Operations resolved over ticks, using strength, equipment, supply, terrain, fortification,
  morale, fatigue, commander traits, and **tactical stance** (Trench Warfare, Blitzkrieg,
  Defense in Depth, and more). The output is a Battle Report email.

### 4.10 Classified Inbox & Event System `[P2]`
The inbox is the heart of the game. Module: `src/engine/event_manager.py`.

**Email template schema** (`data/events/emails.json`):

| Field | Meaning |
|-------|---------|
| `id`, `sender`, `subject`, `classification`, `body` | Content. `body` supports `{nation}`, `{leader_title}`, `{capital}`, `{date}`, `{turn}` and intel placeholders. |
| `arrives_turn` | Schedules the email at game start. Leave it out for follow-up-only templates. |
| `on_arrival` | Effects applied the moment the email is delivered (e.g. a failed op's fallout). |
| `options[]` | Replies: `{id, label, effects, follow_ups}`. |
| `deadline_weeks` + `on_expire` | If unanswered by then, `on_expire` (same shape as an option) is applied automatically. **Silence is a decision.** |
| `intel` | Fog-of-war estimates rendered into the body (see 4.11). |
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

### 4.11 Fog of War `[P2 estimates · P3 units · P4 active]`
- `src/engine/intel.py`. A spec is `{"value": n}` or `{"nation": "vosk", "stat": "manpower", "fraction": f}`,
  plus `accuracy` (0–1).
- Rendered as `LOW – HIGH (CERTAINTY xx%)`. The range width scales with (1 − accuracy), and honest
  estimates are noisy around the truth.
- **Misinformation**: with chance `misinformation_base_chance × (1 − accuracy) × 2` (or a per-spec
  `misinformation_chance`), the estimate is centred on a false value (×0.25–0.6 or ×1.6–3.0)
  **while still claiming the same certainty**. The truth is stored hidden in `email.intel_truth`,
  for later "the report was wrong" reveals.

### 4.12 War Room — Tactical Situation Map `[P3 · P4 orders]`
A digitized staff situation map in the style of WWII Eastern Front and Desert Storm operational
maps: terrain, national territory washes, trench lines, rail, and NATO-style unit symbols.

**Map data: an exact character grid** (`data/map/world.json`, model `src/models/world_map.py`)
- `width` × `height` (currently 128 × 42) terminal cells. Coordinates are **x = column, y = row**, with 0,0 at top-left.
- `base`: the rendered background, `height` rows of exactly `width` characters. It holds borders
  (`│─┼…`), terrain glyphs (`^` mountains, `≈` river/marsh, `#` urban, `.` plains), rail `═║`,
  trenches `┆`, settlements `★` capital, `◉` city, `⊕` port, plus labels, compass and scale bar.
- `regions[]`: `{id, name, owner, terrain, label:[x,y], rects:[[x0,y0,x1,y1], …]}`, with inclusive rects
  and later regions winning on overlap. Cells outside every region are sea. `WorldMap.region_at(x, y)`,
  `owner_at`, and `is_national_border` are exact per-cell lookups.
- `terrain_types{}`: glyph, `movement` and `defense` multipliers, and a description. Shown in the readout
  now, and used by logistics and combat later.
- `features[]`: settlements with exact X/Y.
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
├── main.py                  # python main.py [--skip-boot] [--seed N] [--reveal]
├── requirements.txt / requirements-dev.txt (pytest)
├── GAME_DESIGN.md
├── data/
│   ├── config.json          # dates, economy coefficients, fail-state thresholds, intel, seed
│   ├── nations.json         # starting stats (incl. military_morale)
│   ├── orbat.json           # starting formations with exact map locations
│   ├── ai.json              # AI Director postures, thresholds, flanks, SIGINT
│   ├── resources.json, units.json   # units: symbol, move_speed, detection_radius, loadout
│   ├── equipment.json, tech_tree.json   # PLACEHOLDERS for future R&D / production / loadouts
│   ├── events/emails.json   # email templates + event chains
│   ├── events/system_alerts.json  # SYSTEM PURGE texts per loss cause
│   ├── events/generated.json      # SIGINT, BORDER CLASH, ISOLATED dispatch templates
│   ├── map/world.json       # War Room grid: base art, regions, terrain, features, transport layer
│   └── ui/boot_sequence.json
├── src/
│   ├── models/              # nation.py, inbox.py, game_state.py, military.py (Unit, MoveOrder, Contact),
│   │                        # world_map.py (WorldMap), ai.py (AIState)
│   ├── engine/
│   │   ├── data_loader.py   # new_game(), content validation
│   │   ├── effects.py       # effect interpreter
│   │   ├── intel.py         # fog-of-war estimates (emails + weekly enemy-unit reports)
│   │   ├── map_overlay.py   # visible markers, stacking, ghosts, per-cell lookups
│   │   ├── movement.py      # A* routes, orders, simultaneous movement, skirmish detection
│   │   ├── logistics_engine.py  # supply tracing, ZOC, consumption, attrition
│   │   ├── recon.py         # detection radius, contacts, ghosts (active fog of war)
│   │   ├── ai_director.py   # Vosk DEFEND / PROBE / ASSAULT state machine
│   │   ├── sigint.py        # intercepts of major AI orders
│   │   ├── event_manager.py # delivery, respond(), deadlines, follow-ups
│   │   ├── economy_engine.py# placeholder ledger
│   │   ├── reports.py       # weekly status report
│   │   ├── fail_states.py   # revolution / coup / collapse
│   │   ├── tick_engine.py, systems.py, text.py
│   │   └── combat_engine.py # STUB (Phase 5)
│   └── ui/
│       ├── app.py           # owns GameState + TickEngine, `revision` reactive
│       ├── screens/         # boot.py, terminal.py, confirm.py (reply modal), coordinates.py (grid prompt)
│       ├── widgets/         # status_bar.py (reactive), sidebar.py (nav + ADVANCE WEEK), map_canvas.py (War Room)
│       └── views/           # inbox.py, economy.py, military.py, map.py (War Room + intel panel)
├── tools/generate_world_map.py  # optional: regenerate world.json (overwrites it)
└── tests/                   # test_engine.py, test_ui.py, test_war_room.py, test_phase4.py (fuzzed + headless)
```

### 5.3 Tick order
1. Clock advance
2. **AI Director**: posture, tension, orders (+ SIGINT intercepts), *before* the week resolves
3. **Movement**: both sides march simultaneously; border clashes detected → ENGAGED + dispatch
4. **Recon**: detection, contacts acquired/lost, ghosts
5. **Logistics**: trace supply nets, consume/deliver, attrition, ISOLATED dispatches
6. **Economy**: ledger, treasury, tax discontent, insolvency counter, pay arrears
7. **Events**: expire overdue dispatches (apply `on_expire`), deliver due emails (apply `on_arrival`)
8. **Status report**: this week's Weekly Status & Financial Report (incl. front and supply situation)
9. **Fail states**: revolution / coup / collapse → SYSTEM PURGE + lock

### 5.4 Controls
| Key | Action |
|-----|--------|
| `1`–`4` | Inbox / Economy / Military / Map |
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
| `n` or **▶ ADVANCE WEEK** | Advance one week |
| `q` | Log out |

### 5.5 Testing
`python -m pytest` (about 99 tests, 80 s) runs engine tests, fuzzed campaigns (random replies and random
move orders, with invariants checked every week) and headless Textual tests that drive the real UI:
replies, advance week, the War Room overlay at several terminal sizes, issuing and cancelling orders,
and a live seeded campaign until SIGINT and a border clash arrive. Set `CT_SCREENSHOTS=<dir>` to save
SVG screenshots from the UI tests.

---

## 6. Roadmap

| Phase | Scope |
|-------|-------|
| **1** | Foundation: structure, models, data, TUI shell, boot screen, turn advance, email delivery. ✅ |
| **2** | Inbox loop: replies with effects, confirmation, event chains, deadlines, weekly report, placeholder ledger, fail states, fog-of-war estimates, reactive TUI. ✅ |
| **3** | **War Room**: X/Y grid map, NATO-style symbology, overlay and stacking, pannable Line-API map, sector/intel readout, fog of war on units, and misidentification. ✅ |
| **4** | **Living front**: move orders and A* over a road/rail/trench layer, simultaneous movement, skirmish detection, supply-line logistics (ZOC, isolation, attrition), the Vosk AI Director (DEFEND/PROBE/ASSAULT), active fog of war, SIGINT intercepts. ✅ |
| 5 | Combat engine: casualties, stances, terrain and fortification, commanders, battle-report dispatches, retreats, unit destruction. |
| 6 | Military-Industrial Complex (§7.2): factories, production lines, physical stockpiles; replaces generic supply with specific ammunition. |
| 7 | Unit Equipment Loadouts (§7.3): combat stats derived from inventory; recruitment and training queues. |
| 8 | R&D (§7.1): research slots, tech tree, obsolescence. |
| 9 | Domestic Politics & the Draft (§7.4): war weariness, rationing, conscription laws, factions. |
| 10 | Diplomacy, save/load, balance pass. |

---

## 7. Future Phases — Hardcore Simulator Mechanics `[PLANNED]`

The long-term goal: nothing on the battlefield is abstract. Every rifle, shell and tank is
researched, built in a real factory, shipped along a real supply line, and physically carried by
a formation, and the home front pays for all of it.

### 7.1 Hearts of Iron-style R&D
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

### 7.2 Military-Industrial Complex
- **Factories** (civilian and military) sit in provinces on the map and can be bombed, captured
  or cut off by the logistics network.
- Each military factory is **assigned a production line** for a specific item from
  `data/equipment.json` (e.g. `shell_152_he`, `mbt_medium`). Output depends on factory efficiency
  (it grows the longer a line runs; retooling resets it), raw inputs (steel, fuel, munitions from the
  resource chain), and labour drawn from the manpower pool.
- Output goes into **national stockpiles** of physical items, then travels **along the supply network**
  to depots and formations. Generic "supply %" is replaced by the actual items a unit needs.
- Shortages propagate: no coal → no steel → no shells → artillery falls silent.

### 7.3 Unit Equipment Loadouts
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
- Engaged units currently stay in contact indefinitely (no casualties until Phase 5). The AI does not yet break
  contact on its own.
- Should the player see a supply-flow projection (who will be OVEREXTENDED if a move order completes) before
  confirming an order?
