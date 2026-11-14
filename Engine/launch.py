"""Start Azimuth or run its optional, read-only source checks."""
from __future__ import annotations

import argparse
import importlib
import sys

sys.dont_write_bytecode = True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="dashboard",
                        choices=("dashboard", "check-skins", "check-playthrough"),
                        help="Default: open the control panel.")
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    # Check arguments belong to the check itself, including its --help.
    if args and args[0] in ("check-skins", "check-playthrough"):
        command, remaining = args[0], args[1:]
        if command == "check-skins":
            check_parser = argparse.ArgumentParser(prog=f"{parser.prog} check-skins",
                                                   description="Check the bundled skins without changing your installation.")
            check_parser.parse_args(remaining)
            remaining = []
    else:
        options = parser.parse_args(args)
        command = options.command
        remaining = ["--background"] if options.background else []
    modules = {
        "dashboard": "azimuth_app.ui.dashboard",
        "check-skins": "azimuth_app.checks.verify_gun_skins",
        "check-playthrough": "azimuth_app.checks.verify_playthrough",
    }
    original = sys.argv
    try:
        sys.argv = [original[0], *remaining]
        result = importlib.import_module(modules[command]).main()
        return int(result or 0)
    finally:
        sys.argv = original


if __name__ == "__main__":
    raise SystemExit(main())
