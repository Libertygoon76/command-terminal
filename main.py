"""COMMAND TERMINAL — entry point.

Usage:
    python main.py              # full boot sequence
    python main.py --skip-boot  # jump straight to the desktop (dev convenience)
"""

import argparse

from src.ui.app import CommandTerminalApp


def main() -> None:
    parser = argparse.ArgumentParser(description="Command Terminal — grand strategy simulator")
    parser.add_argument("--skip-boot", action="store_true", help="skip the boot/authentication sequence")
    args = parser.parse_args()

    CommandTerminalApp(skip_boot=args.skip_boot).run()


if __name__ == "__main__":
    main()
