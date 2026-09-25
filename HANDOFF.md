# HANDOFF: continuing the Lead Programmer's work

Written 2026-09-24 by the Claude Code session that built Phases 1-8 and Expansions 1.1-1.2 on the user's PC,
for the cloud session taking over. Read this file, then `CLAUDE.md`, then `GAME_DESIGN.md`.

## 1. What this project is

**Command Terminal**: a hardcore grand-strategy nation simulator played through a 1984 classified
military-terminal TUI. It uses Python 3.12 and Textual, keeps all content in JSON under `data/`, and runs one turn
per week. The player is always the **Commonwealth of Kestria** (ruled by the Lord Protector, capital Aldmark),
fighting the **Vosk Hegemony** (AI, capital Karzan) on a 240×80 continental map. Each map cell is 10 miles.

**The user** (William) is not a programmer and is new to git and GitHub. Explain things plainly and briefly, and do
the technical steps yourself. They run a three-AI studio:
- **Gemini**: Game Director, Lead Systems Designer and Head Writer. It writes the briefs, the systems and the
  narrative text. The user pastes or attaches Gemini's JSON.
- **ChatGPT**: Art & UI Director (layouts, concept art, the Pygame client in `command_graphics/`).
- **Claude Code (you)**: Lead Programmer (engine, UI wiring, tests, bugs).

Implement Gemini's specs faithfully. Text we write ourselves is a placeholder that Gemini replaces.

## 2. Where things stand

The tests pass (**279**). Git history on `master`:

| Commit | What |
|--------|------|
| Phases 1-6 | Foundation, inbox and event chains, War Room map, living front and logistics, war economy, taxes, recruitment and R&D |
| `cfb81bb` Phase 7 | Continental map, navies and blockades, artillery, air wings, weather and frostbite, event deck, 5.56mm |
| `1d77d18` Phase 8 | Commander traits and insubordination, electronic warfare, scorched earth and engineers, victory, save/load |
| `8d3b0ea` Phase 8b | Epidemics, natural disasters, CRITICAL EMERGENCY modals |
| `f49c814` Expansion 1.1 | Foreign powers (Oakhaven, Tor, Vael), lend-lease, 26 living cities, local news wire, the Vosk Hotline |
| `1243ab9` Deck | `tools/generate_massive_deck.py`: 50+ card event deck, event chains, writer-pack translator |
| `b5242d2` Expansion 1.2 | Royal Court: the House of Valerius, cabinet, audiences, treason, marriages, royal generals, succession |
| `f7ab3be` | `CLAUDE.md` |

Nothing is half-built on `master`. **There is no pending task: wait for the user's next brief.**

## 3. Not in the repo (stays on the user's PC)

Someone else (the ChatGPT-driven Pygame work) has **uncommitted** edits on the user's PC to
`command_graphics/app.py`, `atlas.py`, a new `cartography.py`, `GRAPHICAL_CLIENT.md` and
`tests/test_graphical_client.py`. They were deliberately left out of every commit. The cloud copy has the last
committed Pygame client only. Don't rewrite those files unless asked. If the user wants Pygame work in the cloud,
ask them to commit and push those changes from the PC first.

## 4. How the user likes to work

- A brief arrives (often from Gemini), sometimes revised mid-task. **The latest version wins.**
- Build it fully: engine, data, UI, save/load, tests and `GAME_DESIGN.md` (the new section, roadmap row,
  architecture tree, tick order, controls table and open questions). Also update the `README.md` controls.
- **Commit only when asked, with the message they give.** Don't rewrite pushed history. If they ask to commit
  something already committed, say so rather than making an empty or duplicate commit. Keep unrelated
  files (section 3) out of commits.
- **Push to GitHub** after committing (`git push`). The user pulls to their PC from there.
- End each phase with a short report: what was built, anything that couldn't be done and why, and the
  **"how to…" they asked for** (e.g. "how to hold my first Audience"), given as exact keys.
- Flag design choices you had to make yourself (e.g. trait mechanics) so Gemini can confirm them.

## 5. Architecture in one screen (details: `GAME_DESIGN.md` §5)

- `src/models/`: dataclasses only. **Every new model must be listed in `src/models/__init__.__all__`**, or save/load
  cannot encode it. `GameState` holds everything, and `Nation.dynasty` holds the court.
- `src/engine/`: one module per system. `tick_engine.build_default_engine()` runs them in a fixed weekly order: Weather, Command,
  AI, Movement, Air, Combat, Naval, Hotline, Recon, Research, Recruitment, Production, Logistics, Engineering, Frost,
  Crisis, City, Diplomacy, Economy, Events, EW, Court, StatusReport, FailStates, Dilemmas.
  `advance()` raises `DilemmaPendingError` while a modal card is waiting and `GameOverError` after the fall.
- `src/engine/effects.py`: the effect interpreter used by emails, cards, emergencies and audiences. Keys include
  treasury, morale, modifier, relations, build, tech, tax_policy, outbreak, disaster and court.
- `src/engine/savegame.py`: a tagged-JSON codec. `restore()` rebuilds from `new_game()` and overwrites the dynamic
  fields. It is tested for side-by-side determinism.
- `src/ui/`: Textual. Screens (terminal, dilemma modal, game over) and views on keys `1`-`8`, with `0` for the
  Royal Court. Widgets redraw when `app.revision` changes (`app.state_changed()`).
- Runtime modal cards (emergencies, audiences, treason, marriages) go into `state.crisis_cards` and are queued via
  `crisis_engine.raise_emergency()`.
- **Event deck:** `tools/generate_massive_deck.py` holds the placeholder cards, reads Gemini's packs from
  `tools/writer_packs/*.json` (Gemini's own schema, translated), and rewrites `data/events_deck.json` after
  validating it. Re-run it after any deck change.
- **Royal Court:** the cast is `data/dynasty.json` (Gemini's canon, don't edit its text). The mechanics are
  `data/court.json`, and the engine is `src/engine/court.py`.

## 6. Testing

```bash
pip install -r requirements-dev.txt -r requirements-graphics.txt
python -m pytest -q            # ~295 tests, about 4-6 minutes
```

- `tests/conftest.py` autouse `calm_world` keeps tests calm: clear weather, no deck draws, no random crises, and
  `CourtSystem.on_tick` stubbed. To exercise those systems, mark the test `@pytest.mark.live`, or call the real
  function. `tests/test_expansion_1_2.py` captures `REAL_COURT_TICK` at import for this reason.
- `settle_dilemma(state, rng)` answers pending modals in long live campaigns.
- UI tests: `app.run_test(size=...)` plus `pilot.press(...)`. Screenshots: `app.save_screenshot()` (SVG).
- Debug flags: `python main.py --skip-boot --seed N --reveal --event CARD_ID --list-events --crisis outbreak|disaster --load [FILE]`.

## 7. Gotchas already hit

- Textual: never name widget attributes `_timers` or `_render`. A `ContentSwitcher` needs an explicit height.
  Name tcss variables with a `$ct-` prefix (`$panel` and `$surface` clash with the theme).
- Performance: the supply trace works on flat arrays with cached routes. Map damage patches the cost cache
  incrementally (`engineering.invalidate_terrain_caches`). Don't reintroduce whole-map recomputation per tick.
- Balance survey numbers are recorded in `GAME_DESIGN.md`. Re-check them after big changes. Normal taxes should
  survive 40 weeks, and Oppressive taxes should end in revolution.
- When editing with a script, open files for writing only after every check has passed (a crashed script once
  truncated a file to 0 bytes).

## 8. Likely next work

The roadmap (`GAME_DESIGN.md` §6) lists: **Phase 9** (strategic bombing of factories, amphibious landings, multiple
research slots), **Phase 10** (domestic politics and the draft: war weariness, rationing, conscription laws,
factions) and **Phase 11** (diplomacy and a balance pass). §8 holds the open design questions. The Game Director
has ruled on the court questions (GAME_DESIGN.md §C9–C11): trait mechanics confirmed, no Vosk court, royal births are
narrative and morale only, and a ruler under 18 reigns through a Regent (every Treasury cost +10%). The programmer's
own choices in implementing those rulings are listed in §8 for Gemini to confirm.

More writer packs from Gemini should go into `tools/writer_packs/`. Then re-run the generator and the tests. If a
pack uses a key or name the translator doesn't know, the build stops with a clear message. Extend the translator
(and its tests in `tests/test_event_deck.py`) rather than editing Gemini's JSON.
