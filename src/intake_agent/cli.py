"""Command-line entry point: `intake-agent triage` and `intake-agent eval`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import cast, get_args

from intake_agent import __version__
from intake_agent.agent import DEFAULT_MODEL, AgentSettings, ClaudeTriageAgent, Effort
from intake_agent.schema import Enquiry, EnquiryFields

AUTH_HINT = (
    "No Anthropic credentials were found.  Set ANTHROPIC_API_KEY in the environment or in a .env file "
    "in the current directory (see .env.example)."
)


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from a .env file without overriding variables already set."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip("\"'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def read_enquiry(path: Path, args: argparse.Namespace) -> Enquiry:
    """Read a plain-text enquiry, or a JSON file in the Enquiry shape."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return Enquiry.model_validate_json(text)
    received = date.fromisoformat(args.received_date) if args.received_date else date.today()
    return Enquiry(
        enquiry_id=path.stem,
        channel=args.channel,
        received_date=received,
        fields=EnquiryFields(name=args.name, email=args.email),
        text=text,
    )


def agent_settings(args: argparse.Namespace, *, fallbacks: bool) -> AgentSettings:
    effort = args.effort or os.environ.get("INTAKE_AGENT_EFFORT", "medium")
    if effort not in get_args(Effort):
        raise SystemExit(f"Unknown effort {effort!r}.  Use one of {', '.join(get_args(Effort))}.")
    return AgentSettings(
        model=args.model or os.environ.get("INTAKE_AGENT_MODEL", DEFAULT_MODEL),
        effort=cast(Effort, effort),
        max_turns=args.max_turns or int(os.environ.get("INTAKE_AGENT_MAX_TURNS", "8")),
        fallbacks=fallbacks,
    )


def _add_model_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", help=f"Claude model ID (default {DEFAULT_MODEL}, or INTAKE_AGENT_MODEL)")
    parser.add_argument("--effort", choices=get_args(Effort), help="Output effort (default medium)")
    parser.add_argument("--max-turns", type=int, help="Cap on model turns per enquiry (default 8)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="intake-agent",
        description="Triage law-firm client enquiries and evaluate the triage.",
    )
    parser.add_argument("--version", action="version", version=f"intake-agent {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    triage = commands.add_parser("triage", help="Triage one enquiry and print the record as JSON")
    triage.add_argument("path", type=Path, help="A .txt enquiry, or a .json file in the Enquiry shape")
    triage.add_argument("--received-date", help="YYYY-MM-DD the enquiry arrived (default today, .txt only)")
    triage.add_argument("--channel", choices=["web_form", "email"], default="email", help="How it arrived")
    triage.add_argument("--name", help="The enquirer's name from the form, if any")
    triage.add_argument("--email", help="The enquirer's email from the form, if any")
    triage.add_argument(
        "--no-fallbacks",
        action="store_true",
        help="Turn off server-side fallbacks to another model on a refusal",
    )
    triage.add_argument(
        "--no-audit", action="store_true", help="Leave the audit trail out of the printed record"
    )
    _add_model_options(triage)
    return parser


def run_triage(args: argparse.Namespace) -> int:
    enquiry = read_enquiry(args.path, args)
    agent = ClaudeTriageAgent(settings=agent_settings(args, fallbacks=not args.no_fallbacks))
    try:
        record = agent.triage(enquiry)
    except TypeError as exc:
        if "authentication" in str(exc):
            print(AUTH_HINT, file=sys.stderr)
            return 2
        raise
    output = record.model_dump(mode="json", exclude={"audit"} if args.no_audit else None)
    print(json.dumps(output, indent=2))
    if record.failures:
        kinds = ", ".join(sorted({failure.kind for failure in record.failures}))
        print(f"The triage did not complete ({kinds}), so the record routes to a person.", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "triage":
        return run_triage(args)
    raise AssertionError(f"Unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
