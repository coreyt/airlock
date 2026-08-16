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
import tempfile
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv

from airlock.capability import capability_record
from airlock.litellm_config import resolve_litellm_direct_config
from airlock.models_catalog import fetch_live_provider_models
from airlock.startup_validation import (
    credential_without_alias_warnings,
    emit_provider_credential_warnings,
)

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
    print(
        "ERROR: config.yaml not found. Set AIRLOCK_CONFIG or place it in the project root.",
        file=sys.stderr,
    )
    sys.exit(1)


def _validate_mcp_env_refs(config_path: str) -> list[str]:
    """Check that os.environ/ references in mcp_servers have values set.

    Returns a list of human-readable error messages for missing vars.
    """
    # Off mode removes MCP servers from the runtime config. Validating their
    # credentials first makes the documented startup escape hatch unusable,
    # including for a minimal Docker liveness smoke test.
    if _mcp_startup_mode() == "off":
        return []

    cfg = resolve_litellm_direct_config(config_path)

    errors: list[str] = []
    mcp_servers = cfg.get("mcp_servers") or {}
    for server_name, server_cfg in mcp_servers.items():
        if not isinstance(server_cfg, dict):
            continue
        env_block = server_cfg.get("env") or {}
        for _key, value in env_block.items():
            if not isinstance(value, str) or not value.startswith(_ENV_REF_PREFIX):
                continue
            var_name = value[len(_ENV_REF_PREFIX) :]
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
        print(
            "WARNING: AIRLOCK_MASTER_KEY is not set. The proxy will accept unauthenticated requests.",
            file=sys.stderr,
        )
    elif key == _DEFAULT_MASTER_KEY:
        print(
            "WARNING: AIRLOCK_MASTER_KEY is set to the default value. Change it before deploying to production.",
            file=sys.stderr,
        )
    elif len(key) < _MIN_KEY_LENGTH:
        print(
            f"WARNING: AIRLOCK_MASTER_KEY is shorter than {_MIN_KEY_LENGTH} characters. Use a stronger key in production.",
            file=sys.stderr,
        )


def _ssl_cli_args() -> list[str]:
    """Native TLS passthrough flags for the litellm/uvicorn subprocess.

    Native HTTPS is opt-in: ``AIRLOCK_SSL_CERTFILE`` and ``AIRLOCK_SSL_KEYFILE``
    must *both* be set (and non-blank) to serve HTTPS on ``AIRLOCK_HOST:PORT``.
    When unset, the proxy serves plain HTTP and TLS is expected to be terminated by
    a reverse proxy. Per CC-12 this is a parent-process concern (a uvicorn flag),
    not a subprocess store setting. Setting only one of the two is a
    misconfiguration: no TLS is enabled and a warning is emitted.
    """
    cert = os.getenv("AIRLOCK_SSL_CERTFILE", "").strip()
    key = os.getenv("AIRLOCK_SSL_KEYFILE", "").strip()
    if cert and key:
        return ["--ssl_certfile_path", cert, "--ssl_keyfile_path", key]
    if cert or key:
        print(
            "WARNING: only one of AIRLOCK_SSL_CERTFILE / AIRLOCK_SSL_KEYFILE is set; "
            "native TLS requires both. Serving plain HTTP.",
            file=sys.stderr,
        )
    return []


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _startup_model_discovery_enabled() -> bool:
    """Provider model discovery is informational only, so keep it opt-in."""
    return _env_flag("AIRLOCK_STARTUP_MODEL_DISCOVERY", default=False)


def _mcp_startup_mode() -> Literal["off", "lazy", "eager"]:
    value = os.getenv("AIRLOCK_MCP_STARTUP_MODE")
    if value:
        normalized = value.strip().lower()
        if normalized in {"off", "lazy", "eager"}:
            return normalized  # type: ignore[return-value]

    legacy = os.getenv("AIRLOCK_ENABLE_MCP_SERVERS")
    if legacy is not None:
        return "eager" if _env_flag("AIRLOCK_ENABLE_MCP_SERVERS") else "off"

    return "lazy"


def _background_health_checks_override() -> bool | None:
    value = os.getenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS")
    if value is None:
        return None
    return _env_flag("AIRLOCK_BACKGROUND_HEALTH_CHECKS", default=False)


def _fathom_logger_enabled() -> bool:
    return _env_flag("AIRLOCK_ENABLE_FATHOM_LOGGER", default=False)


def _prepare_runtime_config(config_path: str) -> tuple[str, str | None]:
    """Materialize the one canonical config file consumed by the LiteLLM child."""
    config = resolve_litellm_direct_config(config_path)

    mcp_mode = _mcp_startup_mode()
    general_settings = config.setdefault("general_settings", {})

    for entry in config.get("model_list", []) or []:
        if not isinstance(entry, dict):
            continue
        entry.setdefault("model_info", {}).update(capability_record(entry))

    master_key_ref = general_settings.get("master_key")
    if isinstance(master_key_ref, str) and master_key_ref.startswith(_ENV_REF_PREFIX):
        master_key_env = master_key_ref[len(_ENV_REF_PREFIX) :]
        if not os.getenv(master_key_env):
            general_settings.pop("master_key", None)

    if mcp_mode == "off" and config.get("mcp_servers"):
        config.pop("mcp_servers", None)
        print(
            "Configured MCP servers disabled for startup (AIRLOCK_MCP_STARTUP_MODE=off)."
        )
    elif mcp_mode == "lazy" and config.get("mcp_servers"):
        print(
            "Configured MCP servers enabled in lazy startup mode (startup tool discovery suppressed)."
        )

    background_override = _background_health_checks_override()
    if background_override is not None:
        if general_settings.get("background_health_checks") != background_override:
            general_settings["background_health_checks"] = background_override
        mode = "enabled" if background_override else "disabled"
        print(f"Background health checks {mode} by AIRLOCK_BACKGROUND_HEALTH_CHECKS.")

    if _fathom_logger_enabled():
        # The recorder (airlock.callbacks.recorder) owns fathom dispatch now, gated by
        # the same AIRLOCK_ENABLE_FATHOM_LOGGER flag — no config-callback append needed.
        print("Fathom logger enabled for startup (AIRLOCK_ENABLE_FATHOM_LOGGER=1).")

    # Always write a private runtime file: it is the single configuration
    # authority for LiteLLM, child Admin policy, model metadata, and Slice 40's
    # immutable projection. It also works for read-only packaged configs.
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".yaml",
        prefix="airlock-runtime-",
        delete=False,
    )
    try:
        with tmp:
            yaml.safe_dump(config, tmp, sort_keys=False)
    except Exception:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    return tmp.name, tmp.name


def _validate_config(config_path: str) -> list[str]:
    """Validate config.yaml schema and return a list of warning strings."""
    try:
        cfg = resolve_litellm_direct_config(config_path)
    except (ValueError, yaml.YAMLError) as e:
        return [f"config.yaml is not valid YAML: {e}"]

    warnings: list[str] = []

    # model_list
    model_list = cfg.get("model_list")
    if model_list is None or not isinstance(model_list, list):
        warnings.append(
            "model_list is missing or not a list — no models will be available"
        )
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
                warnings.append(
                    f"model_list[{i}] ({name}): missing 'litellm_params.model'"
                )

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
                    warnings.append(
                        f"guardrails[{i}] ({name}): missing 'litellm_params.guardrail'"
                    )

    # mcp_servers
    mcp_servers = cfg.get("mcp_servers")
    if mcp_servers is not None and isinstance(mcp_servers, dict):
        for name, server_cfg in mcp_servers.items():
            if not isinstance(server_cfg, dict):
                continue
            transport = server_cfg.get("transport", "stdio")
            if transport == "stdio" and not server_cfg.get("command"):
                warnings.append(
                    f"mcp_servers.{name}: 'command' is required for stdio transport"
                )

    # general_settings type checks
    gs = cfg.get("general_settings")
    if gs is not None and isinstance(gs, dict):
        if "port" in gs and not isinstance(gs["port"], int):
            warnings.append(
                f"general_settings.port: expected int, got {type(gs['port']).__name__}"
            )

    return warnings


def _register_shutdown_handlers() -> None:
    """Flush loggers and persist state on SIGTERM (atexit only fires on normal exit)."""

    def _handle_sigterm(signum, frame):
        # Flush S3 logger
        try:
            from airlock.callbacks.s3_logger import proxy_s3_logger

            proxy_s3_logger.flush()
        except Exception:
            pass  # best-effort on shutdown

        # FIX-1: state checkpoint/restore now runs in the litellm CHILD process
        # (airlock.fast.monitor), where spend + breaker state are actually mutated.
        # The launcher must NOT checkpoint here — it holds an empty store and would
        # race the child as a second writer of cb_state.json.

        sys.exit(0)  # triggers atexit handlers too

    signal.signal(signal.SIGTERM, _handle_sigterm)


def _warn_observe_mode() -> None:
    """Log a prominent warning when guardrails are in observe-only mode."""
    mode = os.getenv("AIRLOCK_ENFORCE_MODE", "observe").lower().strip()
    if mode == "observe":
        print(
            "WARNING: Guardrails are in 'observe' mode (AIRLOCK_ENFORCE_MODE not set or set to 'observe'). "
            "Guardrails will log but NOT block requests. Set AIRLOCK_ENFORCE_MODE=enforce for production.",
            file=sys.stderr,
        )


def _launch_litellm_child(
    config: dict,
    runtime_config_path: str,
    host: str,
    port: int,
    project_root: Path,
) -> int:
    """Configure all parent seams then run the child from the canonical file."""
    from airlock.admin.policy import configure_admin
    from airlock.fast.router import set_router_config
    from airlock.fast.settings import configure_settings
    from airlock.fast.state import configure_breaker
    from airlock.guardrails.overrides import configure_guardrail_overrides
    from airlock.transparency import configure_transparency

    set_router_config(config)
    configure_settings(config)
    from airlock.fast.admission import configure_admission

    configure_admission(config)
    configure_breaker(config)
    configure_admin(config, host=host, tls_enabled=bool(_ssl_cli_args()))
    configure_guardrail_overrides(config)
    configure_transparency(config)
    if _startup_model_discovery_enabled():
        live_models = fetch_live_provider_models(config)
        if live_models:
            providers = sorted({m["id"].split("/")[0] for m in live_models})
            print(
                f"Provider models discovered: {len(live_models)} across {', '.join(providers)}"
            )
    else:
        print(
            "Startup model discovery disabled "
            "(set AIRLOCK_STARTUP_MODEL_DISCOVERY=1 to enable)."
        )

    litellm_bin = str(Path(sys.executable).parent / "litellm")
    litellm_cmd = [
        litellm_bin,
        "--config",
        runtime_config_path,
        "--host",
        host,
        "--port",
        str(port),
    ]
    litellm_cmd += _ssl_cli_args()
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    repo_pythonpath = str(project_root)
    env["PYTHONPATH"] = (
        f"{repo_pythonpath}:{existing_pythonpath}"
        if existing_pythonpath
        else repo_pythonpath
    )
    env["AIRLOCK_LITELLM_CHILD"] = "1"
    env["AIRLOCK_CONFIG"] = runtime_config_path
    _register_shutdown_handlers()
    print(f"Airlock starting on {host}:{port}")
    return subprocess.run(litellm_cmd, check=False, env=env).returncode


def main() -> None:
    """Launch Airlock proxy entrypoint.

    This function loads environment configuration, validates startup
    inputs, applies runtime config rewrites, and then launches LiteLLM on
    the requested host and port.

    Returns
    -------
    None
        This function does not return normally; it exits the process with
        LiteLLM's exit code.
    """
    project_root = Path(__file__).resolve().parent.parent
    _project_env = project_root / ".env"
    load_dotenv(_project_env)

    _validate_master_key()
    _warn_observe_mode()

    # Initialize the datastore engine if FathomDB is installed

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

    # Default to loopback for safety; deployments that need to expose the
    # proxy externally must set AIRLOCK_HOST=0.0.0.0 explicitly.
    host = os.getenv("AIRLOCK_HOST", "127.0.0.1")
    port = int(os.getenv("AIRLOCK_PORT", "4000"))

    # Log live provider models at startup (informational — does not affect routing).
    try:
        emit_provider_credential_warnings(
            credential_without_alias_warnings(config_path, os.getenv)
        )
    except ValueError:
        # Validation is advisory: never expose a path/parser detail or make an
        # already-valid launch depend on a second local read.
        print(
            "WARNING: airlock.startup.provider_credential_validation_unavailable "
            "source=startup_validation",
            file=sys.stderr,
        )
    # Every parent consumer below and the LiteLLM child use this one materialized
    # file. Keep it before Admin's CC-12 check so an included admin block cannot
    # produce a launcher/child split.
    runtime_config_path, temp_config_path = _prepare_runtime_config(config_path)
    try:
        config = resolve_litellm_direct_config(runtime_config_path)
        sys.exit(
            _launch_litellm_child(config, runtime_config_path, host, port, project_root)
        )
    finally:
        try:
            Path(temp_config_path).unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    main()
