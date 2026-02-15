"""``airlock`` — unified CLI entry point."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the appropriate subcommand."""
    parser = argparse.ArgumentParser(
        prog="airlock",
        description="Airlock — enterprise LLM proxy with guardrails and logging.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # -- init --
    init_parser = subparsers.add_parser(
        "init",
        help="Generate config.yaml, .env, and logs/ in the target directory.",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files.",
    )
    init_parser.add_argument(
        "--dir",
        default=".",
        help="Target directory (default: current directory).",
    )

    # -- start --
    start_parser = subparsers.add_parser(
        "start",
        help="Launch the Airlock LiteLLM proxy.",
    )
    start_parser.add_argument(
        "--host",
        default=None,
        help="Bind address (default: AIRLOCK_HOST or 0.0.0.0).",
    )
    start_parser.add_argument(
        "--port",
        default=None,
        help="Bind port (default: AIRLOCK_PORT or 4000).",
    )
    start_parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: auto-detect).",
    )

    # -- analyze --
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Run offline log analysis.",
    )
    analyze_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days of logs to analyze (default: 7).",
    )
    analyze_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output raw JSON instead of formatted text.",
    )
    analyze_parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Write report to file instead of stdout.",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    if args.command == "init":
        from airlock.cli.init_cmd import run

        run(args)

    elif args.command == "start":
        if args.host is not None:
            os.environ["AIRLOCK_HOST"] = args.host
        if args.port is not None:
            os.environ["AIRLOCK_PORT"] = args.port
        if args.config is not None:
            os.environ["AIRLOCK_CONFIG"] = args.config

        from airlock.proxy import main as proxy_main

        proxy_main()

    elif args.command == "analyze":
        # Rebuild sys.argv for the analyze CLI's own argparse
        sys.argv = ["airlock-analyze"]
        if args.days != 7:
            sys.argv.extend(["--days", str(args.days)])
        if args.json_output:
            sys.argv.append("--json")
        if args.output:
            sys.argv.extend(["--output", args.output])

        from airlock.slow.cli import main as analyze_main

        analyze_main()


if __name__ == "__main__":
    main()
