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

### Optional: the Neural Court (talking courtiers)

Courtiers can hold free conversations through a language model running **on your own PC**. It is free and offline,
and needs no account or API key. Without it the game uses its written dialogue, so you can skip this.

1. Install Ollama from <https://ollama.com> and start it (it runs in the background at `localhost:11434`).
2. Download the model once: `ollama pull llama3.2` (about 2 GB; any chat model works if you change `model` in
   `data/neural.json`).
3. Start the game. The Royal Court screen shows **NEURAL COURT ONLINE**.

`CT_NEURAL=off` switches it off for one run.

## Controls

`1`–`8` and `0` switch views · arrows / Enter navigate · `Tab` moves focus · `a`–`d` reply to the open dispatch · `h` hides archived mail · `n` advances the week · `q` logs out.

War Room (`4`): select a friendly unit (cursor or Order of Battle) → `m` → move the cursor → `Enter`
to issue a move order (`Esc` aborts) · `g` type a grid reference · `x` cancel an order · `s` supply overlay · `t` cycle combat stance (DEFEND / ASSAULT / WITHDRAW), or a warship's mission (PATROL / BLOCKADE / BOMBARD). One map row = 10 miles.

Economy (`2`): highlight a production line → `+` / `-` assign or remove a military factory · `0` close the line. · `[` / `]` lower / raise taxes.

Military (`3`): highlight a formation type → `r` raise it · highlight one in training → `x` cancel · highlight a
formation in the field → `f` relieve its commander (costs military morale). Commanders may refuse unsupported
assaults: back attacks with artillery, naval gunfire or air cover.

Research (`5`): highlight a technology → `Enter` start (or switch) · `x` stop.

Air Assets (`6`): highlight a wing → `[` / `]` move it between sectors · `0` recall it to base.

Classified Dilemmas pop up between weeks: press `1`–`3` (or click) to decide. The week cannot advance until you do.
`python main.py --event worker_strike` forces a card at the next week; `python main.py --list-events` lists the deck.

**The home front:** epidemics and natural disasters raise a red CRITICAL EMERGENCY modal (`1`–`3`): fund
quarantine or relief, send in the troops, or ignore it and pay in morale. Research Field Medicine against disease;
send Combat Engineers to rebuild wrecked railways. `python main.py --crisis outbreak` (or `disaster`) forces one.

Diplomacy (`7`): highlight a foreign power → `g` send an envoy (25,000 CR) · `t` sign / cancel a trade agreement ·
highlight a lend-lease package → `b` buy it (it sails to your first open port; keep the harbours open).

Cities (`8`): highlight a city to see its dashboard and local news → `h` hospital · `b` bunker complex · `i` local
industry · `x` cancel the last project.

Royal Court (`0`): the House of Valerius, the cabinet and the chronicle → `a` hold court (grant an audience) ·
highlight a courtier → `f` / `w` / `i` appoint Minister of Finance / War / Head of Intelligence · `d` dismiss · `m`
marry them abroad · `t` talk to them in private (type freely; with the Neural Court online they answer in character and
the conversation moves their loyalty by up to ±5 a week). Military (`3`) → `k` gives the highlighted division to a royal general (royals never disobey,
but a royal killed in action shakes the House). Watch loyalty: below 20, courtiers plot.

**Save / load:** `ctrl+s` (or `F5`) saves the campaign to `savegame.json`; `python main.py --load` resumes it.

`python main.py --reveal` lifts the fog of war and shows the AI's hidden posture (debug).

## Content

Everything in `data/` is editable JSON: starting nation stats, units, resources, emails, the map,
and the boot script. No code changes needed.
