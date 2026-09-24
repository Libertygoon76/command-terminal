# COMMAND TERMINAL — Strategic Command

A native, mouse-driven 2D client for the existing Python simulation. The terminal launcher
and engine remain independent. No external art downloads or game-engine editor are required.

## Run on Windows

From the project directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-graphics.txt
.\.venv\Scripts\python.exe play_graphical.py
```

Or double-click `Play Graphical.cmd` after installing the dependencies.

```powershell
# Open a terminal campaign; graphical saves go to their own file by default.
.\.venv\Scripts\python.exe play_graphical.py --load savegame.json
# Reopen the graphical campaign.
.\.venv\Scripts\python.exe play_graphical.py --load savegame-graphical.json
# Specify both input and output explicitly.
.\.venv\Scripts\python.exe play_graphical.py --load campaign.json --save campaign.json
```

Default output: `savegame-graphical.json` in the working directory. Saves use the shared
engine format and can also be loaded by `main.py --load savegame-graphical.json`.
Engine version compatibility, including old-save migrations, remains owned by the engine.

## Controls

| Action | Control |
|---|---|
| Select formation | Left-click its counter or Order of Battle entry |
| Movement order | Select a friendly unit, then right-click its destination |
| Preview movement | Hover terrain with a friendly unit selected |
| Zoom | Mouse wheel over the map |
| Pan | Middle-button drag, or arrow keys |
| Fit map | Home |
| Recenter | Click the minimap |
| City inspector | Click a friendly settlement marker, or use Cities |
| Advance week | N or the top-right button |
| Save | F5, Ctrl+S, or Save |
| Load graphical save | F9 (asks before replacing current progress) |
| Fullscreen | F11 |
| Return to map / exit menu | Escape |
| Scroll lists, documents, decisions | Mouse wheel inside the panel |

Industry assigns factories and changes tax policy. Research starts or pauses projects.
Forces recruits or cancels training. Air assigns wings. Dispatches supports the real reply
effects and deadlines, including hotline messages. Emergencies capture input and block
other orders until decided. End-of-campaign screens allow review and saving.

Political and Terrain modes change the atlas palette. Supply colors friendly counters by
their reported supply percentage; this is a unit-status overlay, not a supply-network map.
National boundaries reflect the simulation's fixed regional ownership.

## Expansion 1.1

The client detects the expansion's city and foreign-affairs state. On pre-expansion engines,
Cities shows the settlement atlas and Diplomacy explains that the expansion is unavailable.

With Expansion 1.1 loaded:

- **Hospital:** Cities → Aldmark (or another city) → Hospital → Build. The listed cost is
  charged immediately and the project enters that city's queue. Advance weeks to complete it.
- **Lend-lease:** Diplomacy → choose a power → use Send envoy until its alignment meets the
  package threshold → Purchase cargo. Scroll down to track your convoys. Prices, relations,
  delivery times and inventories come from the engine; port blockades and submarine losses
  are resolved there.
- Cities includes population, local morale, completed structures, construction queues,
  project cancellation with the engine's refund, and a scrolling local news wire.
- Diplomacy includes alignment, envoys, trade agreements, package contents, eligibility,
  and only the player's convoys. Enemy wing inventories and shipments are never rendered.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_graphical_client.py -q
.\.venv\Scripts\python.exe play_graphical.py --seed 84 --screenshot preview.png
```

The SDL dummy driver renders deterministic screenshots without opening a window. Tests drive
actual pygame input events, check command blocking and intelligence visibility, and verify
that save/load continues identically through the shared TickEngine. Terminal-only test runs
skip the optional graphical tests if pygame is not installed.

## Ownership / handoff for concurrent development

Graphical files are `command_graphics/`, `play_graphical.py`, `Play Graphical.cmd`,
`requirements-graphics.txt`, `GRAPHICAL_CLIENT.md`, and `tests/test_graphical_client.py`.
No changes to `src/engine/`, `src/models/`, `src/ui/`, `data/`, `main.py`, or GAME_DESIGN.md
are required. Please keep these files when committing Expansion 1.1.

- `session.py`: campaign lifecycle, guarded commands and fog-safe contact projections.
- `camera.py`: rectangular-cell projection, zoom, and coordinate conversion.
- `atlas.py`: cached procedural map artwork, transport, counters, route, and minimap.
- `painting.py`: text, clipping, buttons, wrapping and bars.
- `app.py`: event loop, base-game screens, decisions, saves and turn resolution.
- `expansion.py`: optional Cities and Diplomacy screens using `src.engine.cities` and
  `src.engine.diplomacy`. If those APIs change, update this adapter rather than the engine.

Rendering does not advance the campaign RNG. The full GameState is never used as a
serialized display model: hostile units pass through intelligence estimates, and jammed
friendlies pass through their last radio report. Only friendly formations in contact expose
equipment and command controls.

This first release uses a procedural 2D atlas. It does not add real-time simulation,
dynamic territorial ownership, army-group battle plans, or 3D units. Weekly resolution
runs synchronously after drawing a resolving status; very expensive ticks may briefly
pause input. The campaign's existing rules remain authoritative.
