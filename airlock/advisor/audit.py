"""Advisor action audit log.

Writes structured JSONL to logs/advisor-audit.jsonl so all advisor
actions (queries, config proposals, applied changes) are traceable.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _log_dir() -> Path:
    return Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))


def log_action(
    action_type: str,
    description: str,
    outcome: str,
    model_used: str,
    details: dict[str, Any] | None = None,
    log_dir: str | Path | None = None,
) -> None:
    """Append an audit record to advisor-audit.jsonl.

    Parameters
    ----------
    action_type : str
        Category of action: "query", "config_proposal", "config_apply", "error".
    description : str
        Human-readable summary of what happened.
    outcome : str
        Result: "success", "rejected", "failed", "error".
    model_used : str
        Which LLM model was used for this action.
    details : dict, optional
        Extra structured data (diff, error message, etc.).
    log_dir : str or Path, optional
        Override log directory (for testing). Falls back to AIRLOCK_LOG_DIR.
    """
    target_dir = Path(log_dir) if log_dir else _log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_type": action_type,
        "description": description,
        "outcome": outcome,
        "model_used": model_used,
    }
    if details:
        record["details"] = details

    log_path = target_dir / "advisor-audit.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")
