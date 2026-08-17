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
    tui_parser.add_argument(
        "--remote-admin",
        action="store_true",
        help="Use the restricted TLS capability UI for a container Admin endpoint.",
    )
    tui_parser.add_argument(
        "--admin-token-file",
        default=None,
        help="Protected capability-token file required with --remote-admin.",
    )
    tui_parser.add_argument(
        "--admin-ca-file",
        default=None,
        help="CA bundle required with --remote-admin.",
    )
    tui_parser.add_argument(
        "--fleet-inventory",
        default=None,
        help="Owner-only same-host read-only fleet inventory YAML.",
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
        "--llm",
        action="store_true",
        help="Add advisory LLM analysis if AIRLOCK_ANALYZER_MODEL is configured.",
    )
    analyze_parser.add_argument(
        "--audience", choices=["ops", "security", "executive"], default="ops"
    )
    analyze_parser.add_argument(
        "--semantic-corpus",
        help="JSON corpus for all-versus-adaptive semantic equivalence evidence.",
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

    # -- semantic-report --
    semantic_parser = subparsers.add_parser(
        "semantic-report",
        help="Summarize semantic classifier verdicts from the request logs.",
    )
    semantic_parser.add_argument(
        "--days", type=int, default=7, help="Days of logs to summarize (default: 7)."
    )
    semantic_parser.add_argument(
        "--json",
        action="store_true",
        dest="semantic_json",
        help="Output raw JSON instead of formatted text.",
    )
    semantic_parser.add_argument(
        "--samples",
        type=int,
        default=25,
        help="Max detection samples to list (identifiers only, no prompt text).",
    )
    semantic_parser.add_argument(
        "--output", "-o", default=None, help="Write the report to a file."
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

    # -- config --
    config_parser = subparsers.add_parser(
        "config",
        help="Export or import Airlock configurations (.zip).",
    )
    config_sub = config_parser.add_subparsers(dest="config_action")

    config_export = config_sub.add_parser(
        "export",
        help="Export Airlock configurations to a zip archive.",
    )
    config_export.add_argument(
        "--dir",
        default=".",
        help="Target directory to create the zip file in (default: current directory).",
    )

    config_import = config_sub.add_parser(
        "import",
        help="Import Airlock configurations from a zip archive.",
    )
    config_import.add_argument(
        "file",
        help="Path to the zip file to import.",
    )
    config_import.add_argument(
        "--dir",
        default=".",
        help="Target directory to extract files to (default: current directory).",
    )

    # -- admin --
    admin_parser = subparsers.add_parser(
        "admin",
        help="Admin capability tokens and control-plane operations.",
    )
    admin_sub = admin_parser.add_subparsers(dest="admin_action")
    mint_parser = admin_sub.add_parser(
        "mint-token",
        help="Mint a short-lived HS256 capability token (signed locally).",
    )
    mint_parser.add_argument(
        "--sub",
        required=True,
        help="Subject. For guardrail-skip tokens this MUST be the client's "
        "authenticated key-derived id (key:<last8>).",
    )
    mint_parser.add_argument(
        "--scope",
        action="append",
        default=[],
        dest="scopes",
        required=True,
        help="Scope (repeatable): admin:<op> or guardrail:skip:<name>.",
    )
    mint_parser.add_argument(
        "--ttl",
        default="1h",
        help="Token lifetime: 30m, 1h, 24h, or seconds (default: 1h).",
    )
    erase_parser = admin_sub.add_parser(
        "erase-client",
        help="Erase a client's rows from the FathomDB store (irreversible; "
        "JSONL logs are NOT touched).",
    )
    erase_parser.add_argument(
        "client_id",
        help="Authenticated client id whose rows to erase (key:<last8>, "
        "or no_client for unauthenticated traffic).",
    )
    erase_parser.add_argument(
        "--confirm",
        required=True,
        help="Repeat the client id to confirm. Erasure is irreversible and "
        "must not be a single mistyped word away.",
    )
    erase_parser.add_argument(
        "--host",
        default=None,
        help="Proxy host (default: 127.0.0.1; the operation is loopback-only).",
    )
    erase_parser.add_argument(
        "--port",
        default=None,
        help="Proxy port (default: AIRLOCK_PORT or 4000).",
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

        if args.fleet_inventory:
            if args.remote_admin or args.admin_token_file or args.admin_ca_file:
                parser.error(
                    "tui --fleet-inventory cannot be combined with remote Admin options"
                )
            if (
                args.start
                or args.daemon
                or args.host is not None
                or args.port is not None
            ):
                parser.error(
                    "tui --fleet-inventory cannot start, own, or target a local proxy"
                )
            from airlock.tui.fleet_app import run as fleet_tui_run

            fleet_tui_run(inventory_file=args.fleet_inventory)
            return
        if args.remote_admin:
            if not args.admin_token_file or not args.admin_ca_file:
                parser.error(
                    "tui --remote-admin requires --admin-token-file and --admin-ca-file"
                )
            if args.start or args.daemon:
                parser.error("tui --remote-admin cannot start or own a local proxy")
            from airlock.tui.remote_app import run as remote_tui_run

            remote_tui_run(
                host=host,
                port=port,
                token_file=args.admin_token_file,
                ca_file=args.admin_ca_file,
            )
            return
        if args.admin_token_file or args.admin_ca_file:
            parser.error("Admin credential files require tui --remote-admin")

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
        if args.llm:
            sys.argv.append("--llm")
            sys.argv.extend(["--audience", args.audience])
        if args.semantic_corpus:
            sys.argv.extend(["--semantic-corpus", args.semantic_corpus])

        from airlock.slow.cli import main as analyze_main

        analyze_main()

    elif args.command == "semantic-report":
        import json as _json

        from airlock.semantic_report import build_report, render_text

        report = build_report(days=args.days, max_samples=args.samples)
        rendered = (
            _json.dumps(report.as_dict(), indent=2, default=str)
            if args.semantic_json
            else render_text(report)
        )
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
            print(f"Wrote {args.output}")
        else:
            print(rendered)
        raise SystemExit(0)

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

    elif args.command == "config":
        from airlock.cli.config_cmd import run

        run(args)

    elif args.command == "admin":
        from airlock.cli.admin_cmd import run

        run(args)

    elif args.command == "advise":
        from airlock.cli.advise_cmd import run

        run(args)


if __name__ == "__main__":
    main()
