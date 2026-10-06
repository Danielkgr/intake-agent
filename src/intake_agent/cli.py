"""Command-line entry point."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from intake_agent import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intake-agent",
        description="Triage law-firm client enquiries and evaluate the triage.",
    )
    parser.add_argument("--version", action="version", version=f"intake-agent {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
