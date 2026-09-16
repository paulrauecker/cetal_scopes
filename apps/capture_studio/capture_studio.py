"""Capture studio: multi-instrument single-shot capture and analysis in a browser.

Run against a bench described by a TOML inventory::

    uv run apps/capture_studio/capture_studio.py --config bench.toml

or with synthetic instruments, needing no hardware at all::

    uv run apps/capture_studio/capture_studio.py --demo --instruments 3
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from api import create_app
from config import demo_inventory, load_inventory
from session import StudioSession

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description=(
            "Serve the capture studio: configure every instrument, arm them "
            "together, capture one shot, and analyse it in the browser."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="TOML inventory describing the instruments on the bench.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use synthetic instruments instead of hardware.",
    )
    parser.add_argument(
        "--instruments",
        type=int,
        default=2,
        help="Number of synthetic instruments when --demo is given (default: 2).",
    )
    parser.add_argument(
        "--channels-each",
        type=int,
        default=2,
        help="Channels per synthetic instrument (default: 2).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address.")
    parser.add_argument("--port", type=int, default=8000, help="Bind port.")
    parser.add_argument(
        "--log-level", default="warning", help="uvicorn log level (default: warning)."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Start the server. Returns a process exit status."""
    args = build_parser().parse_args(argv)

    if args.demo and args.config:
        print("give either --config or --demo, not both")
        return 2

    if args.config:
        try:
            inventory = load_inventory(args.config)
        except (FileNotFoundError, ValueError) as exc:
            print(f"cannot load {args.config}: {exc}")
            return 2
        config_path = Path(args.config)
    else:
        if not args.demo:
            print("no --config given; starting with synthetic instruments")
        try:
            inventory = demo_inventory(
                args.instruments, channels_each=args.channels_each
            )
        except ValueError as exc:
            print(str(exc))
            return 2
        config_path = None

    session = StudioSession(inventory, config_path=config_path)
    app = create_app(session)

    print(f"capture studio on http://{args.host}:{args.port}")
    print(f"instruments: {', '.join(item.label for item in inventory.enabled)}")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
