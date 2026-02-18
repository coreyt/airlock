"""PreToolUse hook (Edit|Write) — protect sensitive config files."""

from __future__ import annotations

import os
from pathlib import PurePosixPath

from airlock.hooks._common import block, proceed, read_hook_input

_DEFAULT_PROTECTED = {".env", "config.yaml"}


def _protected_paths() -> set[str]:
    """Build the set of protected filename patterns."""
    paths = set(_DEFAULT_PROTECTED)
    extra = os.getenv("AIRLOCK_PROTECTED_PATHS", "")
    for p in extra.split(","):
        p = p.strip()
        if p:
            paths.add(p)
    return paths


def main() -> None:
    data = read_hook_input()
    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path:
        proceed()

    filename = PurePosixPath(file_path).name
    protected = _protected_paths()

    for pattern in protected:
        if filename == pattern or file_path.endswith(pattern):
            block(
                f"Airlock: editing '{pattern}' is not allowed. "
                "This file is protected by policy. "
                "Remove it from AIRLOCK_PROTECTED_PATHS or use --force "
                "outside of Claude Code to modify it."
            )

    proceed()


if __name__ == "__main__":
    main()
