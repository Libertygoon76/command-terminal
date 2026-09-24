# COMMAND TERMINAL

A grand-strategy / nation-management simulator played through a classified government terminal.
See [GAME_DESIGN.md](GAME_DESIGN.md) for the full design.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
python main.py
```

`python main.py --skip-boot` skips the boot sequence.

Use Windows Terminal (not the legacy console) for correct colors and box-drawing characters.

## Controls

`1`–`4` switch views · arrows / Enter navigate · `Tab` moves focus · `n` ends the turn · `q` logs out.

## Content

Everything in `data/` is editable JSON: starting nation stats, units, resources, emails, the map,
and the boot script. No code changes needed.
