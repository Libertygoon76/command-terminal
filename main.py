"""COMMAND TERMINAL — entry point.

Usage:
    python main.py              # full boot sequence
    python main.py --skip-boot  # jump straight to the desktop (dev convenience)
    python main.py --reveal     # debug: lift the fog of war and show the AI's posture
    python main.py --event worker_strike   # draw this CLASSIFIED DILEMMA at the next week
    python main.py --list-events           # list the event deck
    python main.py --load                  # resume the campaign saved with CTRL+S (savegame.json)
    python main.py --load my_war.json      # resume from a particular save file
    python main.py --crisis outbreak       # debug: an epidemic breaks out at the first week (or: disaster)
"""

import argparse

from src.ui.app import CommandTerminalApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Command Terminal — grand strategy simulator")
    parser.add_argument("--skip-boot", action="store_true", help="skip the boot/authentication sequence")
    parser.add_argument("--seed", type=int, default=None, help="random seed for a reproducible campaign")
    parser.add_argument("--reveal", action="store_true", help="debug: show every enemy unit and the AI's hidden state")
    parser.add_argument("--event", default=None, metavar="CARD_ID",
                        help="draw this event card (data/events_deck.json) when the next week is advanced")
    parser.add_argument("--list-events", action="store_true", help="list the event deck and exit")
    parser.add_argument("--crisis", choices=["outbreak", "disaster"], default=None,
                        help="debug: start an epidemic or a natural disaster when the first week is advanced")
    parser.add_argument("--load", nargs="?", const="", default=None, metavar="SAVEFILE",
                        help="resume a saved campaign (default savegame.json in the game folder)")
    args = parser.parse_args()

    if args.list_events:
        import json
        from pathlib import Path

        deck = json.loads((Path(__file__).parent / "data" / "events_deck.json").read_text(encoding="utf-8"))
        for entry in deck["cards"]:
            print(f"{entry['id']:<20} {entry['title']}")
        return

    if args.load is not None:
        from src.engine.savegame import SaveError, load_game

        try:
            load_game(args.load or None)  # fail fast, before the terminal opens
        except SaveError as error:
            parser.exit(1, f"{error}\n")
    CommandTerminalApp(skip_boot=args.skip_boot, seed=args.seed, reveal=args.reveal, event=args.event,
                       load=args.load, crisis=args.crisis).run()


if __name__ == "__main__":
    main()
