"""
Airlock Proxy — entry point that launches LiteLLM proxy with Airlock config.

Usage:
    # Via the installed script
    airlock

    # Via Python module
    python -m airlock.proxy

    # Or just use litellm directly
    litellm --config config.yaml
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def _find_config() -> str:
    """Locate config.yaml, checking common paths."""
    candidates = [
        Path(os.getenv("AIRLOCK_CONFIG", "config.yaml")),
        Path(__file__).resolve().parent.parent / "config.yaml",
        Path("/etc/airlock/config.yaml"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    print("ERROR: config.yaml not found. Set AIRLOCK_CONFIG or place it in the project root.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    load_dotenv()

    config_path = _find_config()
    host = os.getenv("AIRLOCK_HOST", "0.0.0.0")
    port = os.getenv("AIRLOCK_PORT", "4000")

    cmd = [
        sys.executable, "-m", "litellm",
        "--config", config_path,
        "--host", host,
        "--port", port,
    ]

    print(f"Airlock starting on {host}:{port} with config {config_path}")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
