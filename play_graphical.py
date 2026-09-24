"""Graphical launcher; the existing main.py terminal launcher remains independent."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="COMMAND TERMINAL / Strategic Command")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--load", nargs="?", const="savegame.json", metavar="SAVEFILE")
    parser.add_argument("--save", default="savegame-graphical.json", metavar="SAVEFILE")
    parser.add_argument("--event", metavar="CARD_ID")
    parser.add_argument("--screenshot", type=Path, help="render one frame to PNG and exit (headless)")
    args = parser.parse_args()
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    if args.screenshot:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ["SDL_AUDIODRIVER"] = "dummy"
    try:
        from command_graphics.app import CommandApp
        from command_graphics.session import Session
    except ModuleNotFoundError as error:
        if error.name == "pygame":
            parser.exit(1, "Install graphics first: python -m pip install -r requirements-graphics.txt\n")
        raise
    from src.engine.savegame import SaveError
    try:
        session = Session(seed=args.seed, load=args.load, save_path=args.save)
        if args.event:
            from src.engine.dilemmas import card
            card(session.game, args.event)
            session.game.forced_card = args.event
    except (SaveError, ValueError, OSError) as error:
        parser.exit(1, f"Cannot start campaign: {error}\n")
    app = CommandApp(session)
    try:
        if args.screenshot:
            app.draw()
            import pygame
            pygame.image.save(app.screen, str(args.screenshot))
        else:
            app.run()
    finally:
        app.close()


if __name__ == "__main__":
    main()
