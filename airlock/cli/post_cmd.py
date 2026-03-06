"""``airlock post`` — Power-On Self-Test.

Validates every external dependency (config, providers, storage, guardrails)
and reports a clear pass/fail summary before sending real traffic.
"""

from __future__ import annotations

import enum
import importlib
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class CheckStatus(enum.Enum):
    """Outcome of a single POST check."""

    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    """Result of a single POST check."""

    name: str
    status: CheckStatus
    label: str  # human-readable check name
    detail: str  # e.g. "5 models configured" or "401 Unauthorized"
    duration_ms: float = 0.0
    group: str = ""


@dataclass
class _CheckEntry:
    """Registry entry for a check function."""

    name: str
    label: str
    group: str
    fn: Callable[[dict, bool], CheckResult]
    skip_flag: str = ""  # which --skip-* flag controls this


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

_CHECKS: list[_CheckEntry] = []


def _register(
    name: str,
    label: str,
    group: str,
    skip_flag: str = "",
) -> Callable:
    """Decorator to register a check function."""

    def decorator(fn: Callable) -> Callable:
        _CHECKS.append(_CheckEntry(name=name, label=label, group=group, fn=fn, skip_flag=skip_flag))
        return fn

    return decorator


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _find_config_path() -> Path:
    """Resolve config.yaml location using the same logic as ``airlock start``."""
    if "AIRLOCK_CONFIG" in os.environ:
        return Path(os.environ["AIRLOCK_CONFIG"])
    return Path("config.yaml")


def _load_config(path: Path) -> dict:
    """Parse config.yaml, returning the top-level dict."""
    import yaml  # lazy — only needed here

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _extract_provider(model_entry: dict) -> str | None:
    """Extract the provider prefix from a model_list entry.

    E.g. ``anthropic/claude-sonnet-4-20250514`` → ``"anthropic"``.
    """
    params = model_entry.get("litellm_params", {})
    model_str = params.get("model", "")
    if "/" in model_str:
        return model_str.split("/", 1)[0]
    return None


def _extract_env_key_ref(model_entry: dict) -> str | None:
    """Extract env var name from ``api_key: os.environ/VAR_NAME``."""
    params = model_entry.get("litellm_params", {})
    api_key = params.get("api_key", "")
    if isinstance(api_key, str) and api_key.startswith("os.environ/"):
        return api_key.split("/", 1)[1]
    return None


# ---------------------------------------------------------------------------
# Config group checks
# ---------------------------------------------------------------------------


@_register("config_file", "Config file", "Config")
def check_config_file(config: dict, verbose: bool) -> CheckResult:
    path = _find_config_path()
    if not path.is_file():
        return CheckResult(
            name="config_file",
            status=CheckStatus.FAIL,
            label="Config file",
            detail=f"not found at {path}",
            group="Config",
        )

    try:
        _load_config(path)
    except Exception as exc:
        return CheckResult(
            name="config_file",
            status=CheckStatus.FAIL,
            label="Config file",
            detail=f"parse error: {exc}",
            group="Config",
        )

    return CheckResult(
        name="config_file",
        status=CheckStatus.PASS,
        label="Config file",
        detail=f"found at {path}",
        group="Config",
    )


@_register("env_file", "Environment file", "Config")
def check_env_file(config: dict, verbose: bool) -> CheckResult:
    config_path = _find_config_path()
    env_path = config_path.parent / ".env"
    if not env_path.is_file():
        return CheckResult(
            name="env_file",
            status=CheckStatus.WARN,
            label="Environment file",
            detail=f".env not found at {env_path}",
            group="Config",
        )
    return CheckResult(
        name="env_file",
        status=CheckStatus.PASS,
        label="Environment file",
        detail=f"found at {env_path}",
        group="Config",
    )


@_register("model_list", "Model list", "Config")
def check_model_list(config: dict, verbose: bool) -> CheckResult:
    models = config.get("model_list")
    if not models or not isinstance(models, list):
        return CheckResult(
            name="model_list",
            status=CheckStatus.FAIL,
            label="Model list",
            detail="empty or missing",
            group="Config",
        )

    # Validate each entry has required fields
    for i, entry in enumerate(models):
        if not isinstance(entry, dict):
            return CheckResult(
                name="model_list",
                status=CheckStatus.FAIL,
                label="Model list",
                detail=f"entry {i} is not a mapping",
                group="Config",
            )
        if "model_name" not in entry:
            return CheckResult(
                name="model_list",
                status=CheckStatus.FAIL,
                label="Model list",
                detail=f"entry {i} missing model_name",
                group="Config",
            )

    count = len(models)
    return CheckResult(
        name="model_list",
        status=CheckStatus.PASS,
        label="Model list",
        detail=f"{count} model{'s' if count != 1 else ''} configured",
        group="Config",
    )


# ---------------------------------------------------------------------------
# Provider group checks
# ---------------------------------------------------------------------------


@_register("provider_keys", "Provider API keys", "Providers", skip_flag="skip_llm")
def check_provider_keys(config: dict, verbose: bool) -> CheckResult:
    models = config.get("model_list", [])
    if not models:
        return CheckResult(
            name="provider_keys",
            status=CheckStatus.SKIP,
            label="Provider API keys",
            detail="no models configured",
            group="Providers",
        )

    missing: list[str] = []
    seen: set[str] = set()
    for entry in models:
        env_var = _extract_env_key_ref(entry)
        if env_var and env_var not in seen:
            seen.add(env_var)
            if not os.environ.get(env_var):
                missing.append(env_var)

    if missing:
        return CheckResult(
            name="provider_keys",
            status=CheckStatus.FAIL,
            label="Provider API keys",
            detail=f"missing: {', '.join(missing)}",
            group="Providers",
        )

    if not seen:
        return CheckResult(
            name="provider_keys",
            status=CheckStatus.PASS,
            label="Provider API keys",
            detail="no os.environ/ refs in config",
            group="Providers",
        )

    return CheckResult(
        name="provider_keys",
        status=CheckStatus.PASS,
        label="Provider API keys",
        detail=f"{len(seen)} key{'s' if len(seen) != 1 else ''} set",
        group="Providers",
    )


def _has_provider(config: dict, provider: str) -> bool:
    """Return True if any model in config uses the given provider prefix."""
    for entry in config.get("model_list", []):
        if _extract_provider(entry) == provider:
            return True
    return False


def _get_api_key_for_provider(config: dict, provider: str) -> str | None:
    """Get the API key value for a given provider from env."""
    for entry in config.get("model_list", []):
        if _extract_provider(entry) == provider:
            env_var = _extract_env_key_ref(entry)
            if env_var:
                return os.environ.get(env_var)
            # Inline key (not os.environ/ ref)
            params = entry.get("litellm_params", {})
            key = params.get("api_key", "")
            if key and not key.startswith("os.environ/"):
                return key
    return None


@_register("provider_anthropic", "Anthropic API", "Providers", skip_flag="skip_llm")
def check_provider_anthropic(config: dict, verbose: bool) -> CheckResult:
    if not _has_provider(config, "anthropic"):
        return CheckResult(
            name="provider_anthropic",
            status=CheckStatus.SKIP,
            label="Anthropic API",
            detail="no Anthropic models configured",
            group="Providers",
        )

    api_key = _get_api_key_for_provider(config, "anthropic")
    if not api_key:
        return CheckResult(
            name="provider_anthropic",
            status=CheckStatus.SKIP,
            label="Anthropic API",
            detail="API key not set",
            group="Providers",
        )

    # 1-token completion to verify auth
    t0 = time.monotonic()
    try:
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        ctx = ssl.create_default_context()
        urllib.request.urlopen(req, timeout=15, context=ctx)  # noqa: S310
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_anthropic",
            status=CheckStatus.PASS,
            label="Anthropic API",
            detail=f"authenticated ({elapsed:.0f}ms)",
            duration_ms=elapsed,
            group="Providers",
        )
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_anthropic",
            status=CheckStatus.FAIL,
            label="Anthropic API",
            detail=f"{exc.code} {exc.reason}",
            duration_ms=elapsed,
            group="Providers",
        )
    except (urllib.error.URLError, OSError) as exc:
        elapsed = (time.monotonic() - t0) * 1000
        reason = str(getattr(exc, "reason", exc))
        return CheckResult(
            name="provider_anthropic",
            status=CheckStatus.WARN,
            label="Anthropic API",
            detail=f"connection error: {reason}",
            duration_ms=elapsed,
            group="Providers",
        )


@_register("provider_openai", "OpenAI API", "Providers", skip_flag="skip_llm")
def check_provider_openai(config: dict, verbose: bool) -> CheckResult:
    if not _has_provider(config, "openai"):
        return CheckResult(
            name="provider_openai",
            status=CheckStatus.SKIP,
            label="OpenAI API",
            detail="no OpenAI models configured",
            group="Providers",
        )

    api_key = _get_api_key_for_provider(config, "openai")
    if not api_key:
        return CheckResult(
            name="provider_openai",
            status=CheckStatus.SKIP,
            label="OpenAI API",
            detail="API key not set",
            group="Providers",
        )

    # GET /v1/models (free, just checks auth)
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        ctx = ssl.create_default_context()
        urllib.request.urlopen(req, timeout=15, context=ctx)  # noqa: S310
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_openai",
            status=CheckStatus.PASS,
            label="OpenAI API",
            detail=f"authenticated ({elapsed:.0f}ms)",
            duration_ms=elapsed,
            group="Providers",
        )
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_openai",
            status=CheckStatus.FAIL,
            label="OpenAI API",
            detail=f"{exc.code} {exc.reason}",
            duration_ms=elapsed,
            group="Providers",
        )
    except (urllib.error.URLError, OSError) as exc:
        elapsed = (time.monotonic() - t0) * 1000
        reason = str(getattr(exc, "reason", exc))
        return CheckResult(
            name="provider_openai",
            status=CheckStatus.WARN,
            label="OpenAI API",
            detail=f"connection error: {reason}",
            duration_ms=elapsed,
            group="Providers",
        )


@_register("provider_mistral", "Mistral AI API", "Providers", skip_flag="skip_llm")
def check_provider_mistral(config: dict, verbose: bool) -> CheckResult:
    if not _has_provider(config, "mistral"):
        return CheckResult(
            name="provider_mistral",
            status=CheckStatus.SKIP,
            label="Mistral AI API",
            detail="no Mistral models configured",
            group="Providers",
        )

    api_key = _get_api_key_for_provider(config, "mistral")
    if not api_key:
        return CheckResult(
            name="provider_mistral",
            status=CheckStatus.SKIP,
            label="Mistral AI API",
            detail="API key not set",
            group="Providers",
        )

    # GET /v1/models (free, just checks auth — same pattern as OpenAI)
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        ctx = ssl.create_default_context()
        urllib.request.urlopen(req, timeout=15, context=ctx)  # noqa: S310
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_mistral",
            status=CheckStatus.PASS,
            label="Mistral AI API",
            detail=f"authenticated ({elapsed:.0f}ms)",
            duration_ms=elapsed,
            group="Providers",
        )
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_mistral",
            status=CheckStatus.FAIL,
            label="Mistral AI API",
            detail=f"{exc.code} {exc.reason}",
            duration_ms=elapsed,
            group="Providers",
        )
    except (urllib.error.URLError, OSError) as exc:
        elapsed = (time.monotonic() - t0) * 1000
        reason = str(getattr(exc, "reason", exc))
        return CheckResult(
            name="provider_mistral",
            status=CheckStatus.WARN,
            label="Mistral AI API",
            detail=f"connection error: {reason}",
            duration_ms=elapsed,
            group="Providers",
        )


@_register("provider_gemini", "Google Gemini API", "Providers", skip_flag="skip_llm")
def check_provider_gemini(config: dict, verbose: bool) -> CheckResult:
    if not _has_provider(config, "gemini"):
        return CheckResult(
            name="provider_gemini",
            status=CheckStatus.SKIP,
            label="Google Gemini API",
            detail="no Gemini models configured",
            group="Providers",
        )

    api_key = _get_api_key_for_provider(config, "gemini")
    if not api_key:
        return CheckResult(
            name="provider_gemini",
            status=CheckStatus.SKIP,
            label="Google Gemini API",
            detail="API key not set",
            group="Providers",
        )

    # 1-token generation to verify auth (x-goog-api-key header avoids key in URL)
    t0 = time.monotonic()
    try:
        payload = json.dumps({
            "contents": [{"parts": [{"text": "hi"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        }).encode()
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
        )
        ctx = ssl.create_default_context()
        urllib.request.urlopen(req, timeout=15, context=ctx)  # noqa: S310
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_gemini",
            status=CheckStatus.PASS,
            label="Google Gemini API",
            detail=f"authenticated ({elapsed:.0f}ms)",
            duration_ms=elapsed,
            group="Providers",
        )
    except urllib.error.HTTPError as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="provider_gemini",
            status=CheckStatus.FAIL,
            label="Google Gemini API",
            detail=f"{exc.code} {exc.reason}",
            duration_ms=elapsed,
            group="Providers",
        )
    except (urllib.error.URLError, OSError) as exc:
        elapsed = (time.monotonic() - t0) * 1000
        reason = str(getattr(exc, "reason", exc))
        return CheckResult(
            name="provider_gemini",
            status=CheckStatus.WARN,
            label="Google Gemini API",
            detail=f"connection error: {reason}",
            duration_ms=elapsed,
            group="Providers",
        )


# ---------------------------------------------------------------------------
# Storage group checks
# ---------------------------------------------------------------------------


@_register("log_dir", "Log directory", "Storage", skip_flag="skip_storage")
def check_log_dir(config: dict, verbose: bool) -> CheckResult:
    log_dir = os.environ.get("AIRLOCK_LOG_DIR", "./logs")
    path = Path(log_dir)

    if not path.is_dir():
        return CheckResult(
            name="log_dir",
            status=CheckStatus.FAIL,
            label="Log directory",
            detail=f"{path} does not exist",
            group="Storage",
        )

    if not os.access(path, os.W_OK):
        return CheckResult(
            name="log_dir",
            status=CheckStatus.FAIL,
            label="Log directory",
            detail=f"{path} is not writable",
            group="Storage",
        )

    return CheckResult(
        name="log_dir",
        status=CheckStatus.PASS,
        label="Log directory",
        detail=f"{path} (writable)",
        group="Storage",
    )


@_register("s3", "S3 bucket", "Storage", skip_flag="skip_storage")
def check_s3(config: dict, verbose: bool) -> CheckResult:
    bucket = os.environ.get("AIRLOCK_S3_BUCKET")
    if not bucket:
        return CheckResult(
            name="s3",
            status=CheckStatus.SKIP,
            label="S3 bucket",
            detail="not configured",
            group="Storage",
        )

    try:
        import boto3  # noqa: F811

        client = boto3.client("s3")
        client.head_bucket(Bucket=bucket)
        return CheckResult(
            name="s3",
            status=CheckStatus.PASS,
            label="S3 bucket",
            detail=f"{bucket} (accessible)",
            group="Storage",
        )
    except ImportError:
        return CheckResult(
            name="s3",
            status=CheckStatus.WARN,
            label="S3 bucket",
            detail="boto3 not installed",
            group="Storage",
        )
    except Exception as exc:
        return CheckResult(
            name="s3",
            status=CheckStatus.FAIL,
            label="S3 bucket",
            detail=str(exc),
            group="Storage",
        )


@_register("sql", "SQL database", "Storage", skip_flag="skip_storage")
def check_sql(config: dict, verbose: bool) -> CheckResult:
    sql_url = os.environ.get("AIRLOCK_SQL_URL")
    if not sql_url:
        return CheckResult(
            name="sql",
            status=CheckStatus.SKIP,
            label="SQL database",
            detail="not configured",
            group="Storage",
        )

    try:
        import sqlalchemy

        engine = sqlalchemy.create_engine(sql_url)
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        return CheckResult(
            name="sql",
            status=CheckStatus.PASS,
            label="SQL database",
            detail="connected",
            group="Storage",
        )
    except ImportError:
        return CheckResult(
            name="sql",
            status=CheckStatus.WARN,
            label="SQL database",
            detail="sqlalchemy not installed",
            group="Storage",
        )
    except Exception as exc:
        return CheckResult(
            name="sql",
            status=CheckStatus.FAIL,
            label="SQL database",
            detail=str(exc),
            group="Storage",
        )


# ---------------------------------------------------------------------------
# Guardrails group checks
# ---------------------------------------------------------------------------


@_register("presidio", "Presidio PII engine", "Guardrails", skip_flag="skip_guardrails")
def check_presidio(config: dict, verbose: bool) -> CheckResult:
    t0 = time.monotonic()
    try:
        from presidio_analyzer import AnalyzerEngine

        AnalyzerEngine()
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="presidio",
            status=CheckStatus.PASS,
            label="Presidio PII engine",
            detail=f"loaded ({elapsed:.0f}ms)",
            duration_ms=elapsed,
            group="Guardrails",
        )
    except (ImportError, OSError) as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(
            name="presidio",
            status=CheckStatus.WARN,
            label="Presidio PII engine",
            detail=f"not available: {exc}",
            duration_ms=elapsed,
            group="Guardrails",
        )


@_register("keywords", "Keyword blocklist", "Guardrails", skip_flag="skip_guardrails")
def check_keywords(config: dict, verbose: bool) -> CheckResult:
    raw = os.environ.get("AIRLOCK_BLOCKED_KEYWORDS", "")
    if not raw.strip():
        return CheckResult(
            name="keywords",
            status=CheckStatus.WARN,
            label="Keyword blocklist",
            detail="AIRLOCK_BLOCKED_KEYWORDS not set",
            group="Guardrails",
        )

    keywords = [k.strip() for k in raw.split(",") if k.strip()]
    if not keywords:
        return CheckResult(
            name="keywords",
            status=CheckStatus.WARN,
            label="Keyword blocklist",
            detail="no valid keywords after parsing",
            group="Guardrails",
        )

    count = len(keywords)
    return CheckResult(
        name="keywords",
        status=CheckStatus.PASS,
        label="Keyword blocklist",
        detail=f"{count} keyword{'s' if count != 1 else ''} configured",
        group="Guardrails",
    )


@_register("guardrail_modules", "Guardrail modules", "Guardrails", skip_flag="skip_guardrails")
def check_guardrail_modules(config: dict, verbose: bool) -> CheckResult:
    guardrails = config.get("guardrails", [])
    if not guardrails:
        return CheckResult(
            name="guardrail_modules",
            status=CheckStatus.SKIP,
            label="Guardrail modules",
            detail="none configured",
            group="Guardrails",
        )

    failed: list[str] = []
    for entry in guardrails:
        params = entry.get("litellm_params", {})
        fqn = params.get("guardrail", "")
        if not fqn:
            continue
        # Split "airlock.guardrails.pii_guard.AirlockPIIGuard" into module + class
        parts = fqn.rsplit(".", 1)
        if len(parts) != 2:
            failed.append(fqn)
            continue
        module_path, class_name = parts
        try:
            mod = importlib.import_module(module_path)
            if not hasattr(mod, class_name):
                failed.append(fqn)
        except Exception:
            failed.append(fqn)

    if failed:
        return CheckResult(
            name="guardrail_modules",
            status=CheckStatus.FAIL,
            label="Guardrail modules",
            detail=f"import failed: {', '.join(failed)}",
            group="Guardrails",
        )

    count = len(guardrails)
    return CheckResult(
        name="guardrail_modules",
        status=CheckStatus.PASS,
        label="Guardrail modules",
        detail=f"{count} guardrail{'s' if count != 1 else ''} importable",
        group="Guardrails",
    )


# ---------------------------------------------------------------------------
# MCP group checks
# ---------------------------------------------------------------------------


@_register("mcp_config", "MCP server config", "MCP", skip_flag="skip_mcp")
def check_mcp_config(config: dict, verbose: bool) -> CheckResult:
    mcp_servers = config.get("mcp_servers")
    if not mcp_servers:
        return CheckResult(
            name="mcp_config",
            status=CheckStatus.SKIP,
            label="MCP server config",
            detail="no mcp_servers configured (optional)",
            group="MCP",
        )
    if not isinstance(mcp_servers, dict):
        return CheckResult(
            name="mcp_config",
            status=CheckStatus.FAIL,
            label="MCP server config",
            detail="mcp_servers must be a dict (server_name: config)",
            group="MCP",
        )
    count = len(mcp_servers)
    return CheckResult(
        name="mcp_config",
        status=CheckStatus.PASS,
        label="MCP server config",
        detail=f"{count} MCP server{'s' if count != 1 else ''} configured",
        group="MCP",
    )


@_register("mcp_server_health", "MCP server health", "MCP", skip_flag="skip_mcp")
def check_mcp_server_health(config: dict, verbose: bool) -> CheckResult:
    mcp_servers = config.get("mcp_servers")
    if not mcp_servers or not isinstance(mcp_servers, dict):
        return CheckResult(
            name="mcp_server_health",
            status=CheckStatus.SKIP,
            label="MCP server health",
            detail="no mcp_servers configured",
            group="MCP",
        )

    import shutil
    from airlock.tui.mcp_manager import _resolve_health_url, probe_http

    healthy: list[str] = []
    unhealthy: list[str] = []
    for name, srv in mcp_servers.items():
        managed = srv.get("airlock_managed") if isinstance(srv.get("airlock_managed"), dict) else None
        url = _resolve_health_url(srv, managed)

        if url:
            ok, latency = probe_http(url, timeout=5.0)
            if ok:
                healthy.append(f"{name} ({latency:.0f}ms)")
            else:
                unhealthy.append(name)
        elif srv.get("command"):
            # stdio — check binary exists
            if shutil.which(srv["command"]):
                healthy.append(f"{name} (binary found)")
            else:
                unhealthy.append(f"{name} (binary not found)")
        else:
            unhealthy.append(f"{name} (no url or command)")

    if unhealthy:
        return CheckResult(
            name="mcp_server_health",
            status=CheckStatus.WARN,
            label="MCP server health",
            detail=f"{len(healthy)} healthy, {len(unhealthy)} unreachable: {', '.join(unhealthy)}",
            group="MCP",
        )

    count = len(healthy)
    return CheckResult(
        name="mcp_server_health",
        status=CheckStatus.PASS,
        label="MCP server health",
        detail=f"{count} server{'s' if count != 1 else ''} healthy",
        group="MCP",
    )


@_register("mcp_managed_config", "MCP managed server config", "MCP", skip_flag="skip_mcp")
def check_mcp_managed_config(config: dict, verbose: bool) -> CheckResult:
    mcp_servers = config.get("mcp_servers")
    if not mcp_servers or not isinstance(mcp_servers, dict):
        return CheckResult(
            name="mcp_managed_config",
            status=CheckStatus.SKIP,
            label="MCP managed server config",
            detail="no mcp_servers configured",
            group="MCP",
        )

    import shutil

    managed = {n: s for n, s in mcp_servers.items() if isinstance(s.get("airlock_managed"), dict)}
    if not managed:
        return CheckResult(
            name="mcp_managed_config",
            status=CheckStatus.SKIP,
            label="MCP managed server config",
            detail="no airlock_managed servers",
            group="MCP",
        )

    issues: list[str] = []
    for name, srv in managed.items():
        mcfg = srv["airlock_managed"]
        cmd = mcfg.get("command", "")
        if not cmd:
            issues.append(f"{name}: missing command")
        elif not shutil.which(cmd):
            issues.append(f"{name}: command '{cmd}' not found")
        cwd = mcfg.get("cwd", "")
        if cwd and not Path(cwd).expanduser().is_dir():
            issues.append(f"{name}: cwd does not exist: {cwd}")

    if issues:
        return CheckResult(
            name="mcp_managed_config",
            status=CheckStatus.WARN,
            label="MCP managed server config",
            detail="; ".join(issues),
            group="MCP",
        )

    count = len(managed)
    return CheckResult(
        name="mcp_managed_config",
        status=CheckStatus.PASS,
        label="MCP managed server config",
        detail=f"{count} managed server{'s' if count != 1 else ''} valid",
        group="MCP",
    )


@_register("mcp_guardrail_hooks", "MCP guardrail hooks", "MCP", skip_flag="skip_guardrails")
def check_mcp_guardrail_hooks(config: dict, verbose: bool) -> CheckResult:
    guardrails = config.get("guardrails", [])
    if not guardrails:
        return CheckResult(
            name="mcp_guardrail_hooks",
            status=CheckStatus.SKIP,
            label="MCP guardrail hooks",
            detail="no guardrails configured",
            group="MCP",
        )

    mcp_modes = {"pre_mcp_call", "during_mcp_call"}
    mcp_registered = []
    for entry in guardrails:
        params = entry.get("litellm_params", {})
        mode = params.get("mode", "")
        modes = mode if isinstance(mode, list) else [mode]
        if any(m in mcp_modes for m in modes):
            mcp_registered.append(entry.get("guardrail_name", "?"))

    if not mcp_registered:
        return CheckResult(
            name="mcp_guardrail_hooks",
            status=CheckStatus.WARN,
            label="MCP guardrail hooks",
            detail="no guardrails registered for MCP hooks",
            group="MCP",
        )

    count = len(mcp_registered)
    return CheckResult(
        name="mcp_guardrail_hooks",
        status=CheckStatus.PASS,
        label="MCP guardrail hooks",
        detail=f"{count} guardrail{'s' if count != 1 else ''} with MCP hooks",
        group="MCP",
    )


# ---------------------------------------------------------------------------
# Runner — execute checks with per-check timeout
# ---------------------------------------------------------------------------


def _run_check_with_timeout(
    entry: _CheckEntry,
    config: dict,
    verbose: bool,
    timeout: float,
) -> CheckResult:
    """Run a check function with a timeout using a daemon thread."""
    result_holder: list[CheckResult] = []
    error_holder: list[Exception] = []

    def _target() -> None:
        try:
            result_holder.append(entry.fn(config, verbose))
        except Exception as exc:
            error_holder.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        return CheckResult(
            name=entry.name,
            status=CheckStatus.FAIL,
            label=entry.label,
            detail=f"timed out after {timeout:.0f}s",
            group=entry.group,
        )

    if error_holder:
        return CheckResult(
            name=entry.name,
            status=CheckStatus.FAIL,
            label=entry.label,
            detail=f"error: {error_holder[0]}",
            group=entry.group,
        )

    return result_holder[0]


def run_checks(
    *,
    skip_llm: bool = False,
    skip_storage: bool = False,
    skip_guardrails: bool = False,
    skip_mcp: bool = False,
    verbose: bool = False,
    timeout: float = 30.0,
) -> list[CheckResult]:
    """Execute all registered checks, returning ordered results."""
    skip_flags = set()
    if skip_llm:
        skip_flags.add("skip_llm")
    if skip_storage:
        skip_flags.add("skip_storage")
    if skip_guardrails:
        skip_flags.add("skip_guardrails")
    if skip_mcp:
        skip_flags.add("skip_mcp")

    # Load config once for all checks
    config_path = _find_config_path()
    config: dict = {}
    if config_path.is_file():
        try:
            config = _load_config(config_path)
        except Exception:
            pass  # config_file check will report the error

    results: list[CheckResult] = []
    for entry in _CHECKS:
        if entry.skip_flag and entry.skip_flag in skip_flags:
            results.append(
                CheckResult(
                    name=entry.name,
                    status=CheckStatus.SKIP,
                    label=entry.label,
                    detail="skipped by flag",
                    group=entry.group,
                )
            )
            continue

        result = _run_check_with_timeout(entry, config, verbose, timeout)
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

# ANSI color codes
_COLORS = {
    CheckStatus.PASS: "\033[32m",  # green
    CheckStatus.FAIL: "\033[31m",  # red
    CheckStatus.WARN: "\033[33m",  # yellow
    CheckStatus.SKIP: "\033[2m",   # dim
}
_RESET = "\033[0m"


def _format_status_tag(status: CheckStatus, use_color: bool) -> str:
    """Format [PASS], [FAIL], etc. with optional ANSI color."""
    tag = f"[{status.value}]"
    if use_color:
        return f"{_COLORS[status]}{tag}{_RESET}"
    return tag


def render_text(results: list[CheckResult], *, use_color: bool = True) -> str:
    """Render results as human-readable text output."""
    lines: list[str] = []
    lines.append("")
    lines.append("  Airlock POST \u2014 Power-On Self-Test")
    lines.append("")

    # Group results
    current_group = ""
    for r in results:
        if r.group != current_group:
            if current_group:
                lines.append("")
            lines.append(f"  {r.group}")
            current_group = r.group

        tag = _format_status_tag(r.status, use_color)
        lines.append(f"    {tag}  {r.label:<32s} {r.detail}")

    # Summary
    counts = {s: 0 for s in CheckStatus}
    for r in results:
        counts[r.status] += 1

    lines.append("")
    parts = [
        f"{counts[CheckStatus.PASS]} passed",
        f"{counts[CheckStatus.FAIL]} failed",
        f"{counts[CheckStatus.WARN]} warned",
        f"{counts[CheckStatus.SKIP]} skipped",
    ]
    lines.append(f"  Results: {', '.join(parts)}")

    overall = "PASS" if counts[CheckStatus.FAIL] == 0 else "FAIL"
    if use_color:
        color = _COLORS[CheckStatus.PASS] if overall == "PASS" else _COLORS[CheckStatus.FAIL]
        lines.append(f"  Status:  {color}{overall}{_RESET}")
    else:
        lines.append(f"  Status:  {overall}")
    lines.append("")

    return "\n".join(lines)


def render_json(results: list[CheckResult]) -> str:
    """Render results as machine-readable JSON."""
    counts = {s: 0 for s in CheckStatus}
    for r in results:
        counts[r.status] += 1

    output = {
        "checks": [
            {
                "name": r.name,
                "group": r.group,
                "status": r.status.value,
                "label": r.label,
                "detail": r.detail,
                "duration_ms": r.duration_ms,
            }
            for r in results
        ],
        "summary": {
            "passed": counts[CheckStatus.PASS],
            "failed": counts[CheckStatus.FAIL],
            "warned": counts[CheckStatus.WARN],
            "skipped": counts[CheckStatus.SKIP],
            "status": "PASS" if counts[CheckStatus.FAIL] == 0 else "FAIL",
        },
    }
    return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def run(args: Any) -> None:
    """Execute the ``airlock post`` command."""
    results = run_checks(
        skip_llm=getattr(args, "skip_llm", False),
        skip_storage=getattr(args, "skip_storage", False),
        skip_guardrails=getattr(args, "skip_guardrails", False),
        skip_mcp=getattr(args, "skip_mcp", False),
        verbose=getattr(args, "verbose", False),
        timeout=getattr(args, "timeout", 30.0),
    )

    if getattr(args, "json_output", False):
        print(render_json(results))
    else:
        use_color = not getattr(args, "no_color", False)
        # Also disable color if NO_COLOR env var is set (standard)
        if os.environ.get("NO_COLOR"):
            use_color = False
        print(render_text(results, use_color=use_color))

    # Exit 1 if any FAIL
    has_failure = any(r.status == CheckStatus.FAIL for r in results)
    raise SystemExit(1 if has_failure else 0)
