"""
Airlock Proxy — launches LiteLLM directly on the configured host and port.

At startup, live provider model counts are logged for informational purposes.
GET /v1/models is served by LiteLLM natively (alias names come from model_list
in config.yaml).

Usage:
    airlock start           # via the installed CLI
    python -m airlock.proxy # directly
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from airlock.models_catalog import fetch_live_provider_models

_ENV_REF_PREFIX = "os.environ/"


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


def _validate_mcp_env_refs(config_path: str) -> list[str]:
    """Check that os.environ/ references in mcp_servers have values set.

    Returns a list of human-readable error messages for missing vars.
    """
    with open(config_path) as f:
        cfg = yaml.safe_load(f) or {}

    errors: list[str] = []
    mcp_servers = cfg.get("mcp_servers") or {}
    for server_name, server_cfg in mcp_servers.items():
        if not isinstance(server_cfg, dict):
            continue
        env_block = server_cfg.get("env") or {}
        for _key, value in env_block.items():
            if not isinstance(value, str) or not value.startswith(_ENV_REF_PREFIX):
                continue
            var_name = value[len(_ENV_REF_PREFIX):]
            if not os.environ.get(var_name):
                errors.append(
                    f"  MCP server '{server_name}' requires {var_name} "
                    f"(set in .env or shell environment)"
                )
    return errors


def main() -> None:
    _project_env = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_project_env)

    config_path = _find_config()

    mcp_errors = _validate_mcp_env_refs(config_path)
    if mcp_errors:
        print(
            "ERROR: Missing environment variables for MCP servers:\n"
            + "\n".join(mcp_errors)
            + "\n\nSet these in your .env file or export them in your shell.",
            file=sys.stderr,
        )
        sys.exit(1)

    host = os.getenv("AIRLOCK_HOST", "0.0.0.0")
    port = int(os.getenv("AIRLOCK_PORT", "4000"))

    # Log live provider models at startup (informational — does not affect routing).
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    live_models = fetch_live_provider_models(config)
    if live_models:
        providers = sorted({m["id"].split("/")[0] for m in live_models})
        print(f"Provider models discovered: {len(live_models)} across {', '.join(providers)}")

    litellm_bin = str(Path(sys.executable).parent / "litellm")
    litellm_cmd = [
        litellm_bin,
        "--config", config_path,
        "--host", host,
        "--port", str(port),
    ]

    print(f"Airlock starting on {host}:{port}")
    sys.exit(subprocess.call(litellm_cmd))


if __name__ == "__main__":
    main()
