# Command Terminal — notes for Claude Code sessions

A hardcore 1984 grand-strategy nation simulator: Python 3.12 + Textual TUI, all content in `data/*.json`,
1 turn = 1 week. The player is the Commonwealth of Kestria against the Vosk Hegemony (AI).
`GAME_DESIGN.md` is the source of truth for the design, architecture, tick order and roadmap. Read it before a phase.

## Setup (fresh machine / cloud)

```bash
pip install -r requirements-dev.txt -r requirements-graphics.txt
python -m pytest -q          # ~325 tests, about 4-6 minutes
python main.py --skip-boot   # play (terminal UI)
```

The Pygame client test skips itself if `pygame-ce` is missing; it runs headless with `SDL_VIDEODRIVER=dummy`.
The Neural Court (Expansion 2.0) needs a local Ollama server to talk (`ollama pull llama3.2`); without one the game
falls back to scripted text. Tests never contact it (conftest sets `CT_NEURAL=off`; the neural tests mock `requests`).

## Team

- **Gemini**: Game Director, Lead Systems Designer and Head Writer. Its content arrives as JSON: the event packs
  go to `tools/writer_packs/`, the cast to `data/dynasty.json`. Implement its specs faithfully. Any prose we write
  ourselves is a placeholder.
- **ChatGPT**: Art & UI Director (layouts, concept art, the Pygame client in `command_graphics/`).
- **Claude Code**: Lead Programmer (engine logic, UI wiring, tests, bugs).

## Conventions

- Engines (`src/engine/`) are separate from the UI (`src/ui/`). The UI redraws on `app.revision` / `state_changed()`.
- New models must be exported in `src/models/__init__.__all__`, or save/load cannot encode them.
- `tests/conftest.py` holds the world calm unless a test is marked `@pytest.mark.live`. Calm means clear weather,
  no event deck, no random crises and no court intrigue. To test those systems directly, call the real function.
- Event deck: edit `tools/generate_massive_deck.py` or add a writer pack, then run
  `python tools/generate_massive_deck.py`. It validates the deck and rewrites `data/events_deck.json`.
- The user supplies commit messages. Commit only when asked, and never rewrite pushed history.
