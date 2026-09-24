# COMMAND TERMINAL — Game Design Document

> Living document. Update it whenever a system is designed, changed, or cut.
> Last major revision: Phase 2 (inbox loop, event chains, fail states), 2026-09-23.

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
 │ Read inbox   │ -> │ Review dash-   │ -> │ Reply to dispat- │ -> │ ADVANCE WEEK │
 │ (reports,    │    │ boards (econ,  │    │ ches (effects +  │    │ (n / button) │
 │  dilemmas)   │    │  military, map)│    │ event chains)    │    │              │
 └──────────────┘    └────────────────┘    └──────────────────┘    └──────┬───────┘
        ^                                                                 │
        │  economy → logistics → events (deadlines, deliveries) →         │
        │  weekly status report → fail-state check                       │
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

### 4.4 Military & Logistics `[STUB]`
- Recruitment → training (`training_weeks`) → equipment → supply. Formations consume rations,
  fuel and munitions every week (`upkeep` in `units.json`).
- Out-of-supply effects escalate: fatigue → attrition → starvation → desertion → surrender.

### 4.5 Large-Scale Combat `[STUB]`
- Operations resolved over ticks, using strength, equipment, supply, terrain, fortification,
  morale, fatigue, commander traits, and **tactical stance** (Trench Warfare, Blitzkrieg,
  Defense in Depth, and more). The output is a Battle Report email.

### 4.6 Classified Inbox & Event System `[P2]`
The inbox is the heart of the game. Module: `src/engine/event_manager.py`.

**Email template schema** (`data/events/emails.json`):

| Field | Meaning |
|-------|---------|
| `id`, `sender`, `subject`, `classification`, `body` | Content. `body` supports `{nation}`, `{leader_title}`, `{capital}`, `{date}`, `{turn}` and intel placeholders. |
| `arrives_turn` | Schedules the email at game start. Leave it out for follow-up-only templates. |
| `on_arrival` | Effects applied the moment the email is delivered (e.g. a failed op's fallout). |
| `options[]` | Replies: `{id, label, effects, follow_ups}`. |
| `deadline_weeks` + `on_expire` | If unanswered by then, `on_expire` (same shape as an option) is applied automatically. **Silence is a decision.** |
| `intel` | Fog-of-war estimates rendered into the body (see 4.7). |
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

### 4.7 Fog of War `[P2 intel estimates]`
- `src/engine/intel.py`. A spec is `{"value": n}` or `{"nation": "vosk", "stat": "manpower", "fraction": f}`,
  plus `accuracy` (0–1).
- Rendered as `LOW – HIGH (CERTAINTY xx%)`. The range width scales with (1 − accuracy), and honest
  estimates are noisy around the truth.
- **Misinformation**: with chance `misinformation_base_chance × (1 − accuracy) × 2` (or a per-spec
  `misinformation_chance`), the estimate is centred on a false value (×0.25–0.6 or ×1.6–3.0)
  **while still claiming the same certainty**. The truth is stored hidden in `email.intel_truth`,
  for later "the report was wrong" reveals.

### 4.8 Map `[P1 static]` → War Room `[P3]`
- Phase 1: a static ASCII map plus a region table. Phase 3 turns this into the War Room (§4.9 once built).

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
├── main.py                  # python main.py [--skip-boot] [--seed N]
├── requirements.txt / requirements-dev.txt (pytest)
├── GAME_DESIGN.md
├── data/
│   ├── config.json          # dates, economy coefficients, fail-state thresholds, intel, seed
│   ├── nations.json         # starting stats (incl. military_morale)
│   ├── resources.json, units.json
│   ├── events/emails.json   # email templates + event chains
│   ├── events/system_alerts.json  # SYSTEM PURGE texts per loss cause
│   ├── map/world.json
│   └── ui/boot_sequence.json
├── src/
│   ├── models/              # nation.py, inbox.py (Email, EmailOption, FollowUp), game_state.py
│   ├── engine/
│   │   ├── data_loader.py   # new_game(), content validation
│   │   ├── effects.py       # effect interpreter
│   │   ├── intel.py         # fog-of-war estimates
│   │   ├── event_manager.py # delivery, respond(), deadlines, follow-ups
│   │   ├── economy_engine.py# placeholder ledger
│   │   ├── reports.py       # weekly status report
│   │   ├── fail_states.py   # revolution / coup / collapse
│   │   ├── tick_engine.py, systems.py, text.py
│   │   └── logistics_manager.py, combat_engine.py   # STUBS
│   └── ui/
│       ├── app.py           # owns GameState + TickEngine, `revision` reactive
│       ├── screens/         # boot.py, terminal.py, confirm.py (reply confirmation modal)
│       ├── widgets/         # status_bar.py (reactive), sidebar.py (nav + ADVANCE WEEK)
│       └── views/           # inbox.py, economy.py, military.py, map.py
└── tests/                   # test_engine.py (incl. fuzzed playthroughs), test_ui.py (headless)
```

### 5.3 Tick order
1. Clock advance
2. **Economy**: ledger, treasury, tax discontent, insolvency counter, pay arrears
3. **Logistics**: `[STUB]`
4. **Events**: expire overdue dispatches (apply `on_expire`), deliver due emails (apply `on_arrival`)
5. **Status report**: this week's Weekly Status & Financial Report
6. **Fail states**: revolution / coup / collapse → SYSTEM PURGE + lock

### 5.4 Controls
| Key | Action |
|-----|--------|
| `1`–`4` | Inbox / Economy / Military / Map |
| `↑ ↓ Enter`, `Tab` | Navigate lists / move focus |
| `a`–`d` | Reply to the open dispatch with option A–D (asks to confirm: `y` / `n`) |
| `h` | Hide / show archived dispatches |
| `n` or **▶ ADVANCE WEEK** | Advance one week |
| `q` | Log out |

### 5.5 Testing
`python -m pytest` runs engine tests, 40 fuzzed random playthroughs, and headless Textual tests
that drive the real UI (reply → confirm → reactive header, advance week, event-chain arrival,
revolution lock). Set `CT_SCREENSHOTS=<dir>` to save SVG screenshots from the UI tests.

---

## 6. Roadmap

| Phase | Scope |
|-------|-------|
| **1** | Foundation: structure, models, data, TUI shell, boot screen, turn advance, email delivery. ✅ |
| **2** | Inbox loop: replies with effects, confirmation, event chains, deadlines, weekly report, placeholder ledger, fail states, fog-of-war estimates, reactive TUI. ✅ |
| 3 | **War Room map**: X/Y grid map, NATO-style unit symbology, unit overlay and stacking, pannable map, sector/intel readout with fog of war. |
| 4 | Economy engine: production chains, stockpiles, markets, trade. |
| 5 | Military: recruitment/training queues, formations, equipment. |
| 6 | Logistics: supply lines on the map grid, consumption, attrition. |
| 7 | Combat engine: operations, stances, terrain, battle reports. |
| 8 | AI (Vosk), diplomacy, save/load, balance pass. |

---

## 7. Open Questions
- Real-time-with-pause, or strictly turn-based? (Currently strictly turn-based.)
- Should a SYSTEM PURGE offer "start new campaign" in-app, or only by relaunching?
- Balance: the current event deck runs out after about 8 weeks, and the placeholder economy is gently positive, so
  failure currently only comes from choices. Recurring and triggered events should add pressure.
