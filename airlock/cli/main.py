"""``airlock`` — unified CLI entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


def configure_logging() -> None:
    """Set up file + stderr logging for the airlock package.

    Idempotent — skips if handlers are already attached.
    """
    airlock_logger = logging.getLogger("airlock")
    if airlock_logger.handlers:
        return

    log_dir = Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"airlock-{timestamp}.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(formatter)

    airlock_logger.setLevel(logging.DEBUG)
    airlock_logger.addHandler(fh)
    airlock_logger.addHandler(sh)


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the appropriate subcommand."""
    # Load .env early so AIRLOCK_* vars are available for arg defaults.
    # Explicit path: bare load_dotenv() uses find_dotenv() which walks from
    # CWD — fails when CLI is invoked from a different directory.
    _project_env = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(_project_env)

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
        help="Bind address (default: AIRLOCK_HOST or 127.0.0.1).",
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

    # -- status --
    status_parser = subparsers.add_parser(
        "status",
        help="Check if the Airlock proxy is running.",
    )
    status_parser.add_argument(
        "--host",
        default=None,
        help="Proxy host to probe (default: AIRLOCK_HOST or localhost).",
    )
    status_parser.add_argument(
        "--port",
        default=None,
        help="Proxy port to probe (default: AIRLOCK_PORT or 4000).",
    )

    # -- tui --
    tui_parser = subparsers.add_parser(
        "tui",
        help="Launch the interactive terminal dashboard.",
    )
    tui_parser.add_argument(
        "--host",
        default=None,
        help="Proxy host to monitor (default: AIRLOCK_HOST or localhost).",
    )
    tui_parser.add_argument(
        "--port",
        default=None,
        help="Proxy port to monitor (default: AIRLOCK_PORT or 4000).",
    )
    tui_parser.add_argument(
        "--start",
        action="store_true",
        help="Automatically start the proxy when the TUI launches.",
    )
    tui_parser.add_argument(
        "--daemon",
        action="store_true",
        help="Leave the proxy running after the TUI exits.",
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
        "--output",
        "-o",
        type=str,
        default=None,
        help="Write report to file instead of stdout.",
    )

    # -- hooks --
    hooks_parser = subparsers.add_parser(
        "hooks",
        help="Install or inspect Claude Code hooks.",
    )
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_action")

    hooks_install = hooks_sub.add_parser(
        "install",
        help="Install Airlock hooks into .claude/settings.json.",
    )
    hooks_install.add_argument(
        "--dir",
        default=".",
        help="Target directory (default: current directory).",
    )
    hooks_install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing hooks configuration.",
    )

    hooks_status = hooks_sub.add_parser(
        "status",
        help="Show configured hooks.",
    )
    hooks_status.add_argument(
        "--dir",
        default=".",
        help="Target directory (default: current directory).",
    )

    # -- post --
    post_parser = subparsers.add_parser(
        "post",
        help="Power-On Self-Test — validate config, providers, storage, guardrails.",
    )
    post_parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip LLM provider connectivity checks.",
    )
    post_parser.add_argument(
        "--skip-storage",
        action="store_true",
        help="Skip storage (log dir, S3, SQL) checks.",
    )
    post_parser.add_argument(
        "--skip-guardrails",
        action="store_true",
        help="Skip guardrail dependency checks.",
    )
    post_parser.add_argument(
        "--skip-mcp",
        action="store_true",
        help="Skip MCP server health and config checks.",
    )
    post_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output machine-readable JSON.",
    )
    post_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output.",
    )
    post_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show verbose check details.",
    )
    post_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-check timeout in seconds (default: 30).",
    )

    # -- install-service --
    install_svc_parser = subparsers.add_parser(
        "install-service",
        help="Install Airlock as a systemd user service.",
    )
    install_svc_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print commands without executing.",
    )

    # -- dogfood --
    dogfood_parser = subparsers.add_parser(
        "dogfood",
        help="Print env exports for routing Claude Code through Airlock.",
    )
    dogfood_parser.add_argument(
        "--host",
        default=None,
        help="Proxy host (default: AIRLOCK_HOST or localhost).",
    )
    dogfood_parser.add_argument(
        "--port",
        default=None,
        help="Proxy port (default: AIRLOCK_PORT or 4000).",
    )
    dogfood_parser.add_argument(
        "--master-key",
        default=None,
        help="Master key (default: AIRLOCK_MASTER_KEY).",
    )
    dogfood_parser.add_argument(
        "--shell",
        choices=["bash", "zsh", "fish"],
        default=None,
        help="Shell syntax (default: bash).",
    )

    # -- advise --
    advise_parser = subparsers.add_parser(
        "advise",
        help="Ask the advisor about Airlock operational data.",
    )
    advise_parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Question to ask the advisor.",
    )
    advise_parser.add_argument(
        "--host",
        default=None,
        help="Proxy host (default: AIRLOCK_HOST or localhost).",
    )
    advise_parser.add_argument(
        "--port",
        default=None,
        help="Proxy port (default: AIRLOCK_PORT or 4000).",
    )
    advise_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override advisor model selection.",
    )
    advise_parser.add_argument(
        "--local-only",
        action="store_true",
        default=False,
        help="Only use local models (error if none available).",
    )
    advise_parser.add_argument(
        "--interactive",
        action="store_true",
        default=False,
        help="Start an interactive advisor session.",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    configure_logging()

    if args.command == "init":
        from airlock.cli.init_cmd import run

        run(args)

    elif args.command == "start":
        # Pre-flight validation (FR-22)
        if args.config is not None:
            config_path = Path(args.config)
        elif "AIRLOCK_CONFIG" in os.environ:
            config_path = Path(os.environ["AIRLOCK_CONFIG"])
        else:
            config_path = Path("config.yaml")

        if not config_path.is_file():
            print(
                f"Error: config file not found: {config_path}\n"
                "Run 'airlock init' to generate one.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        env_path = config_path.parent / ".env"
        if not env_path.is_file():
            print(
                f"Warning: .env not found at {env_path} — proceeding without it.",
                file=sys.stderr,
            )

        if args.host is not None:
            os.environ["AIRLOCK_HOST"] = args.host
        if args.port is not None:
            os.environ["AIRLOCK_PORT"] = args.port
        if args.config is not None:
            os.environ["AIRLOCK_CONFIG"] = args.config

        from airlock.proxy import main as proxy_main

        proxy_main()

    elif args.command == "status":
        from airlock.cli.status_cmd import run

        run(args)

    elif args.command == "tui":
        host = args.host or os.environ.get("AIRLOCK_HOST", "localhost")
        port = args.port or os.environ.get("AIRLOCK_PORT", "4000")

        from airlock.tui.app import run as tui_run

        tui_run(
            host=host,
            port=port,
            auto_start=args.start,
            daemon_mode=args.daemon,
        )

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

    elif args.command == "hooks":
        from airlock.cli.hooks_cmd import run_install, run_status

        if args.hooks_action == "install":
            run_install(args)
        elif args.hooks_action == "status":
            run_status(args)
        else:
            hooks_parser.print_help()

    elif args.command == "post":
        from airlock.cli.post_cmd import run

        run(args)

    elif args.command == "install-service":
        from airlock.cli.install_service_cmd import run

        run(args)

    elif args.command == "dogfood":
        from airlock.cli.dogfood_cmd import run

        run(args)

    elif args.command == "advise":
        from airlock.cli.advise_cmd import run

        run(args)


if __name__ == "__main__":
    main()
