# COMMAND TERMINAL

A grand-strategy / nation-management simulator played through a classified government terminal.
See [GAME_DESIGN.md](GAME_DESIGN.md) for the full design.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements-dev.txt
python main.py
```

`python main.py --skip-boot` skips the boot sequence; `--seed N` makes a campaign reproducible.

Run the tests with `python -m pytest`.

Use Windows Terminal (not the legacy console) for correct colors and box-drawing characters.

## Controls

`1`–`4` switch views · arrows / Enter navigate · `Tab` moves focus · `a`–`d` reply to the open dispatch · `h` hides archived mail · `n` advances the week · `q` logs out.

War Room (`4`): select a friendly unit (cursor or Order of Battle) → `m` → move the cursor → `Enter`
to issue a move order (`Esc` aborts) · `g` type a grid reference · `x` cancel an order · `s` supply overlay.

`python main.py --reveal` lifts the fog of war and shows the AI's hidden posture (debug).

## Content

Everything in `data/` is editable JSON: starting nation stats, units, resources, emails, the map,
and the boot script. No code changes needed.
