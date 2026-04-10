"""``airlock hooks`` — install and inspect Claude Code hooks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HOOKS_CONFIG = {
    "SessionStart": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "python -m airlock.hooks.session_start",
                }
            ]
        }
    ],
    "UserPromptSubmit": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "python -m airlock.hooks.pre_submit",
                }
            ]
        }
    ],
    "PreToolUse": [
        {
            "matcher": "Edit|Write",
            "hooks": [
                {
                    "type": "command",
                    "command": "python -m airlock.hooks.pre_tool",
                }
            ],
        }
    ],
    "PostToolUse": [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": "python -m airlock.hooks.post_tool",
                    "async": True,
                }
            ]
        }
    ],
}


def run_install(args) -> None:
    """Install Airlock hooks into .claude/settings.json."""
    target = Path(args.dir).resolve()
    claude_dir = target / ".claude"
    settings_path = claude_dir / "settings.json"
    force: bool = getattr(args, "force", False)

    # Load existing settings (or start fresh)
    existing: dict = {}
    if settings_path.is_file():
        try:
            existing = json.loads(settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Error reading {settings_path}: {exc}", file=sys.stderr)
            raise SystemExit(1)

    # Check for existing hooks
    if "hooks" in existing and not force:
        print(
            "Hooks already configured in .claude/settings.json.\n"
            "Use --force to overwrite.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Merge hooks into existing settings
    existing["hooks"] = _HOOKS_CONFIG

    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

    # Summary
    print()
    print("  Airlock hooks installed:")
    print()
    for event in _HOOKS_CONFIG:
        print(f"    + {event}")
    print()
    print(f"  Settings: {settings_path}")
    print()
    print("  Next steps:")
    print("    1. Set AIRLOCK_BLOCKED_KEYWORDS in your environment")
    print("    2. Run: airlock hooks status")
    print("    3. Start Claude Code in this project directory")
    print()


def run_status(args) -> None:
    """Show configured hooks from .claude/settings.json."""
    target = Path(args.dir).resolve()
    settings_path = target / ".claude" / "settings.json"

    if not settings_path.is_file():
        print("No .claude/settings.json found.", file=sys.stderr)
        print("Run 'airlock hooks install' to set up hooks.", file=sys.stderr)
        raise SystemExit(1)

    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error reading {settings_path}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    hooks = settings.get("hooks", {})
    if not hooks:
        print("No hooks configured in .claude/settings.json.")
        raise SystemExit(0)

    print()
    print("  Configured hooks:")
    print()
    for event, entries in hooks.items():
        for entry in entries:
            matcher = entry.get("matcher", "*")
            for hook in entry.get("hooks", []):
                is_async = hook.get("async", False)
                cmd = hook.get("command", "")
                flags = " (async)" if is_async else ""
                print(f"    {event:<20} [{matcher}] {cmd}{flags}")
    print()
