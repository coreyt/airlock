"""PostToolUse hook (async) — audit logging to JSONL."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from airlock.hooks._common import proceed, read_hook_input

_MAX_OUTPUT_LEN = 2000


def main() -> None:
    log_dir = os.environ.get("AIRLOCK_LOG_DIR", "logs")

    try:
        data = read_hook_input()

        # Truncate large tool output
        tool_output = data.get("tool_output", "")
        if isinstance(tool_output, str) and len(tool_output) > _MAX_OUTPUT_LEN:
            tool_output = tool_output[:_MAX_OUTPUT_LEN] + "... [truncated]"
            data["tool_output"] = tool_output

        now = datetime.now(timezone.utc)
        record = {
            "timestamp": now.isoformat(),
            "hook": "PostToolUse",
            "tool_name": data.get("tool_name", ""),
            "tool_input": data.get("tool_input", {}),
            "tool_output": tool_output,
        }

        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        filename = f"claude-hooks-{now.strftime('%Y-%m-%d')}.jsonl"
        with open(log_path / filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # Never fail — async audit hook

    proceed()


if __name__ == "__main__":
    main()
