# COMMAND TERMINAL — Game Design Document

> Living document. Update it whenever a system is designed, changed, or cut.
> Last major revision: Phase 1 (project foundation), 2026-09-23.

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

## 2. Setting (placeholder — to be confirmed)

- Fictional world, roughly Cold-War-era technology with interwar-style mass land warfare
  (trenches, armor, artillery). Start date defaults to **1984-01-02** (a Monday) — configurable.
- Player nation: **Commonwealth of Kestria** (placeholder name), capital **Aldmark**.
- Primary rival: **Vosk Hegemony**, to the east, across a contested **Frontier** province.
- Neutral sea lane: **Iren Straits**.

All names live in `data/` and can be replaced freely.

---

## 3. Core Loop

```
 ┌──────────────┐    ┌────────────────┐    ┌──────────────────┐    ┌────────────┐
 │ Read inbox   │ -> │ Review dash-   │ -> │ Issue orders:    │ -> │ END TURN   │
 │ (reports,    │    │ boards (econ,  │    │ taxes, edicts,   │    │ (advance 1 │
 │  dilemmas)   │    │  military, map)│    │ builds, armies,  │    │  week)     │
 └──────────────┘    └────────────────┘    │ email replies    │    └─────┬──────┘
        ^                                  └──────────────────┘          │
        │           tick engine runs every system, generates reports     │
        └────────────────────────────────────────────────────────────────┘
```

---

## 4. Systems

Status legend: `[P1]` built in Phase 1 · `[STUB]` placeholder exists · `[PLANNED]` design only.

### 4.1 Time & Tick Engine `[P1]`
- 1 turn = 1 week (`days_per_turn` in `data/config.json`).
- `TickEngine.advance()` moves the clock forward, then runs each registered `SimulationSystem`
  in a fixed order, collecting a `TickReport` (log lines, new messages).
- Systems are independent modules that read/write `GameState`. Order matters and is explicit
  (economy before logistics before morale, etc.).
- Future: seeded RNG stored in `GameState` for reproducible runs and save/load.

### 4.2 Nation Management `[P1 data] [PLANNED logic]`
- **Treasury** (can go negative = debt, with interest and credit-rating effects).
- **Tax rate** — revenue vs. morale and growth.
- **Population** and **Manpower** (the recruitable pool; replenishes from population growth,
  depleted by conscription and casualties).
- **Morale** 0–100: civilian mood. Bands: COLLAPSING (<20), UNREST (<40), STEADY (<70), HIGH.
  Low morale → strikes, lower productivity, desertion, coups.
- **Infrastructure / Construction**: build queues for factories, rail, depots, barracks,
  fortifications. Buildings have cost, build time, upkeep (data: `buildings.json`, planned).
- **Edicts**: persistent policies (rationing, martial law, war bonds, propaganda) with ongoing
  costs and modifiers (data: `edicts.json`, planned).
- Planned additional stats: stability, corruption, war support, political factions.

### 4.3 Deep Trade & Economy `[P1 data] [STUB logic]`
- **Resources** (`data/resources.json`): raw (grain, coal, iron ore, crude oil) and
  manufactured (steel, fuel, munitions, rations).
- **Production chains**: e.g. iron ore + coal → steel; crude oil → fuel; steel + coal → munitions;
  grain → rations. Factories convert inputs to outputs each tick if inputs are available.
- **Stockpiles** per nation; shortages propagate down the chain.
- **Markets**: per-resource supply/demand with price elasticity; prices drift each tick,
  shocks from events (blockades, strikes, harvest failure).
- **Trade routes**: contracts with other nations, vulnerable to blockade/interdiction.
- Module: `src/engine/economy_engine.py`.

### 4.4 Military & Logistics — the core simulator `[P1 data] [STUB logic]`
- **Recruitment drives** draw from Manpower; recruits enter **training** for N weeks
  (`training_weeks` in `units.json`) before becoming usable formations.
- **Equipment**: formations require steel/munitions to equip; under-equipped units fight worse.
- **Supply**: every formation consumes **rations, fuel and munitions** every week
  (`upkeep` in `units.json`). Supply flows from depots along **supply lines** (rail/road/sea).
- **Supply line model** (planned): graph of provinces; throughput limited by infrastructure,
  terrain, weather and enemy interdiction. Distance from depot reduces throughput.
- **Out of supply** effects escalate weekly: fatigue → attrition → starvation → desertion →
  surrender.
- Module: `src/engine/logistics_manager.py`.

### 4.5 Large-Scale Tactical Combat `[STUB]`
- Battles are operations resolved over one or more ticks, not single dice rolls.
- Inputs: strength, equipment, **supply state**, **terrain**, **fortification**, **morale**,
  **fatigue**, **commander traits**, and **tactical stance**.
- Stances (planned, data-driven): *Trench Warfare* (defense ++, attrition both sides),
  *Blitzkrieg* (breakthrough ++, fuel cost ++, overextension risk), *Defense in Depth*,
  *Human Wave*, *Fighting Withdrawal*.
- Output: casualties, equipment losses, ground gained, morale shifts, and a **Battle Report
  email** written from templates.
- Module: `src/engine/combat_engine.py`.

### 4.6 Event System & Classified Inbox `[P1 delivery] [PLANNED effects]`
- All events, dilemmas and reports arrive as **emails** (`data/events/emails.json`).
- Each email has: `id`, `sender`, `subject`, `classification`, `body`, `arrives_turn`,
  and optional `options` (replies).
- Phase 1: scheduled delivery by turn, read/unread tracking, options displayed.
- Planned:
  - Reply options carry `effects` (e.g. `{"treasury": -40000, "morale": 3}`) applied by an
    effect interpreter; some effects schedule follow-up emails (event chains).
  - Trigger conditions instead of fixed turns (`"trigger": {"morale_below": 30}`), weights,
    cooldowns, and one-shot flags.
  - Response deadlines — ignoring an email is itself a choice with consequences.
  - Generated reports (weekly treasury summary, battle reports, intelligence digests).
- Module: `src/engine/event_manager.py`.

### 4.7 Map `[P1 static]`
- ASCII strategic map (`data/map/world.json`) plus a region table (owner, terrain).
- Planned: province graph with adjacency, supply throughput, fronts, unit positions,
  dynamic ownership coloring.

### 4.8 AI Nations `[PLANNED]`
- Rival nations run the same systems as the player (same `Nation` model, same engines).
- Simple utility-based AI for budget, recruitment, and war declaration.

---

## 5. Architecture

### 5.1 Principles
- **Strict separation of logic and UI.** Engines never import from `src/ui`. The UI reads
  `GameState` and calls engine entry points (`TickEngine.advance()`, future command API).
- **Models are dumb data** with trivial invariants (clamping, validation). No simulation math.
- **Engines are pure-ish systems** operating on `GameState`, testable without Textual.
- **All tunable numbers and content in `data/*.json`.**

### 5.2 Directory layout

```
command-terminal/
├── main.py                  # entry point (python main.py [--skip-boot])
├── requirements.txt
├── GAME_DESIGN.md           # this document
├── data/                    # ALL content & balance — edit freely
│   ├── config.json          # start date, turn length, player nation, currency
│   ├── nations.json         # starting stats for every nation
│   ├── resources.json       # raw & manufactured goods
│   ├── units.json           # unit templates: costs, training time, upkeep, combat stats
│   ├── events/emails.json   # scheduled classified emails
│   ├── map/world.json       # ASCII map + regions
│   └── ui/boot_sequence.json# boot screen script
└── src/
    ├── models/              # pure data classes
    │   ├── nation.py        # Nation
    │   ├── inbox.py         # Email, EmailOption, Inbox
    │   └── game_state.py    # GameClock, GameState
    ├── engine/              # simulation logic (no UI imports!)
    │   ├── data_loader.py   # JSON loading, new_game()
    │   ├── text.py          # {placeholder} substitution for data-driven text
    │   ├── systems.py       # SimulationSystem base, TickReport
    │   ├── tick_engine.py   # TickEngine — runs systems each turn
    │   ├── event_manager.py # email delivery (P1), effects (planned)
    │   ├── economy_engine.py    # STUB
    │   ├── logistics_manager.py # STUB
    │   └── combat_engine.py     # STUB
    └── ui/                  # Textual TUI (the "ui_manager" layer)
        ├── app.py           # CommandTerminalApp, theme
        ├── palette.py       # colors + formatting helpers
        ├── styles/terminal.tcss
        ├── screens/boot.py      # boot/authentication sequence
        ├── screens/terminal.py  # main desktop: status bar, sidebar, views
        ├── widgets/status_bar.py
        ├── widgets/sidebar.py
        └── views/           # inbox, economy, military, map panels
```

### 5.3 Tick order (target)
1. Clock advance
2. Economy: production, consumption, market prices, tax income, upkeep
3. Logistics: supply distribution, army consumption, attrition/desertion
4. Combat: resolve ongoing operations
5. Population & morale
6. Events: deliver scheduled/triggered emails, generate reports

### 5.4 Controls (Phase 1)
| Key | Action |
|-----|--------|
| `1`–`4` | Inbox / Economy / Military / Map |
| `↑ ↓ Enter` | Navigate lists |
| `Tab` | Move focus between panels |
| `n` | End turn (advance one week) |
| `q` | Log out (quit) |

---

## 6. Roadmap

| Phase | Scope |
|-------|-------|
| **1** | Foundation: structure, models, data files, TUI shell, boot screen, turn advance, email delivery. ✅ |
| 2 | Email replies with effects; event triggers & chains; weekly treasury report email. |
| 3 | Economy engine: tax income, production chains, stockpiles, market prices, Economy screen controls. |
| 4 | Military: recruitment/training queues, formations, equipment, Military screen controls. |
| 5 | Logistics: province graph, depots, supply lines, consumption, attrition. |
| 6 | Combat engine: operations, stances, terrain, battle reports. |
| 7 | AI nations, diplomacy, save/load, balance pass. |

---

## 7. Open Questions
- Era/setting confirmation (Cold-War tech + interwar tactics? pure fantasy-industrial?).
- Single player nation vs. selectable nations at start?
- Victory / loss conditions (conquest, survival N years, coup/revolution = loss)?
- Should intelligence reports be deliberately unreliable (fog of war) from day one?
- Real-time pause option, or strictly turn-based? (Currently strictly turn-based.)
