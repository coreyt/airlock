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
import signal
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


_DEFAULT_MASTER_KEY = "sk-airlock-change-me"
_MIN_KEY_LENGTH = 16


def _validate_master_key() -> None:
    """Warn on default, weak, or missing master key."""
    key = os.getenv("AIRLOCK_MASTER_KEY", "")
    if not key:
        print("WARNING: AIRLOCK_MASTER_KEY is not set. The proxy will accept unauthenticated requests.", file=sys.stderr)
    elif key == _DEFAULT_MASTER_KEY:
        print("WARNING: AIRLOCK_MASTER_KEY is set to the default value. Change it before deploying to production.", file=sys.stderr)
    elif len(key) < _MIN_KEY_LENGTH:
        print(f"WARNING: AIRLOCK_MASTER_KEY is shorter than {_MIN_KEY_LENGTH} characters. Use a stronger key in production.", file=sys.stderr)


def _validate_config(config_path: str) -> list[str]:
    """Validate config.yaml schema and return a list of warning strings."""
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        return [f"config.yaml is not valid YAML: {e}"]

    warnings: list[str] = []

    # model_list
    model_list = cfg.get("model_list")
    if model_list is None or not isinstance(model_list, list):
        warnings.append("model_list is missing or not a list — no models will be available")
    elif len(model_list) == 0:
        warnings.append("model_list is empty — no models will be available")
    else:
        for i, entry in enumerate(model_list):
            if not isinstance(entry, dict):
                continue
            if not entry.get("model_name"):
                warnings.append(f"model_list[{i}]: missing 'model_name'")
            name = entry.get("model_name", f"index {i}")
            lp = entry.get("litellm_params")
            if not isinstance(lp, dict) or not lp.get("model"):
                warnings.append(f"model_list[{i}] ({name}): missing 'litellm_params.model'")

    # guardrails
    guardrails = cfg.get("guardrails")
    if guardrails is not None:
        if not isinstance(guardrails, list):
            warnings.append("guardrails must be a list")
        else:
            for i, entry in enumerate(guardrails):
                if not isinstance(entry, dict):
                    continue
                if not entry.get("guardrail_name"):
                    warnings.append(f"guardrails[{i}]: missing 'guardrail_name'")
                name = entry.get("guardrail_name", f"index {i}")
                lp = entry.get("litellm_params")
                if not isinstance(lp, dict) or not lp.get("guardrail"):
                    warnings.append(f"guardrails[{i}] ({name}): missing 'litellm_params.guardrail'")

    # mcp_servers
    mcp_servers = cfg.get("mcp_servers")
    if mcp_servers is not None and isinstance(mcp_servers, dict):
        for name, server_cfg in mcp_servers.items():
            if not isinstance(server_cfg, dict):
                continue
            transport = server_cfg.get("transport", "stdio")
            if transport == "stdio" and not server_cfg.get("command"):
                warnings.append(f"mcp_servers.{name}: 'command' is required for stdio transport")

    # general_settings type checks
    gs = cfg.get("general_settings")
    if gs is not None and isinstance(gs, dict):
        if "port" in gs and not isinstance(gs["port"], int):
            warnings.append(f"general_settings.port: expected int, got {type(gs['port']).__name__}")

    return warnings


def _register_shutdown_handlers() -> None:
    """Ensure S3 logger flushes on SIGTERM (atexit only fires on normal exit)."""
    def _handle_sigterm(signum, frame):
        try:
            from airlock.callbacks.s3_logger import proxy_s3_logger
            proxy_s3_logger.flush()
        except Exception:
            pass  # best-effort on shutdown
        sys.exit(0)  # triggers atexit handlers too
    signal.signal(signal.SIGTERM, _handle_sigterm)


def main() -> None:
    _project_env = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_project_env)

    _validate_master_key()

    config_path = _find_config()

    config_warnings = _validate_config(config_path)
    for warning in config_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

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

    _register_shutdown_handlers()
    print(f"Airlock starting on {host}:{port}")
    sys.exit(subprocess.call(litellm_cmd))


if __name__ == "__main__":
    main()
