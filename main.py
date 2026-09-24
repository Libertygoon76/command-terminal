"""COMMAND TERMINAL — entry point.

Usage:
    python main.py              # full boot sequence
    python main.py --skip-boot  # jump straight to the desktop (dev convenience)
    python main.py --reveal     # debug: lift the fog of war and show the AI's posture
"""

import argparse

from src.ui.app import CommandTerminalApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Command Terminal — grand strategy simulator")
    parser.add_argument("--skip-boot", action="store_true", help="skip the boot/authentication sequence")
    parser.add_argument("--seed", type=int, default=None, help="random seed for a reproducible campaign")
    parser.add_argument("--reveal", action="store_true", help="debug: show every enemy unit and the AI's hidden state")
    args = parser.parse_args()

    CommandTerminalApp(skip_boot=args.skip_boot, seed=args.seed, reveal=args.reveal).run()


if __name__ == "__main__":
    main()
