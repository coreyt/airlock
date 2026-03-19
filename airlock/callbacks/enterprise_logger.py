"""
Airlock Enterprise Logger — LiteLLM custom callback.

Captures every LLM request and response and writes structured JSON logs
that can be shipped to Splunk, Datadog, S3, or any SIEM.

Usage in config.yaml:
    litellm_settings:
        success_callback: ["airlock.callbacks.enterprise_logger"]
        failure_callback: ["airlock.callbacks.enterprise_logger"]

Env vars:
    AIRLOCK_LOG_DIR  — directory for JSON log files (default: ./logs)
"""

from __future__ import annotations

import json
import logging
import os
import datetime
from pathlib import Path
from typing import Any

from airlock.client_identity import extract_airlock_client_from_kwargs
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("airlock.logger")

def _log_dir() -> Path:
    return Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))


def _ensure_log_dir() -> Path:
    d = _log_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _serialize(obj: Any) -> Any:
    """Make objects JSON-serializable."""
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if hasattr(obj, "model_dump"):  # pydantic v2
        return obj.model_dump()
    if hasattr(obj, "dict"):  # pydantic v1
        return obj.dict()
    return str(obj)


def _get_airlock_client(metadata: dict[str, Any], kwargs: dict[str, Any]) -> str | None:
    """Return the best available Airlock client identifier."""
    return (
        metadata.get("airlock_client")
        or extract_airlock_client_from_kwargs(kwargs)
        or os.getenv("AIRLOCK_CLIENT")
        or metadata.get("client_id")
    )


def _normalize_failure(
    kwargs: dict[str, Any],
    response_obj: Any,
) -> tuple[str, str | None, str]:
    """Return a stable failure message, type, and category.

    Categories:
      - provider: request reached the model/provider layer and failed there
      - eval: request failed before a provider response was produced
    """
    exc = kwargs.get("exception")
    exc_type = type(exc).__name__ if exc is not None else None

    error_text = ""
    if exc is not None:
        error_text = str(exc).strip()
        if not error_text:
            exc_repr = repr(exc).strip()
            empty_repr = f"{type(exc).__name__}()"
            if exc_repr != empty_repr:
                error_text = exc_repr

    messages = kwargs.get("messages") or []
    last_content = ""
    if messages:
        last = messages[-1]
        if isinstance(last, dict):
            content = last.get("content", "")
            last_content = str(content)

    provider_failure = bool(error_text)
    if response_obj is not None:
        provider_failure = True

    if provider_failure:
        category = "provider"
    elif "Evaluation question:" in last_content:
        category = "eval"
    else:
        category = "eval"

    if error_text:
        return error_text, exc_type, category

    synthetic = "No exception details captured before provider call"
    if "Evaluation question:" in last_content:
        synthetic = "Evaluation request failed before provider call"
    return synthetic, exc_type, category


def _write_log(record: dict[str, Any]) -> None:
    """Append a JSON record to today's log file."""
    log_dir = _ensure_log_dir()
    today = datetime.date.today().isoformat()
    log_path = log_dir / f"airlock-{today}.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=_serialize) + "\n")


class AirlockLogger(CustomLogger):
    """LiteLLM callback that logs requests/responses to structured JSON files.

    LiteLLM's ``get_instance_fn`` returns whatever ``getattr`` finds at the
    dotted path — it does **not** instantiate classes.  The module-level
    ``proxy_logger`` instance below is the object that config.yaml should
    reference so that ``isinstance(callback, CustomLogger)`` passes inside
    LiteLLM's logging pipeline.
    """

    # ------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------
    def log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        record = self._build_record(kwargs, response_obj, start_time, end_time, success=True)
        _write_log(record)
        logger.info("request_logged model=%s user=%s tokens=%s", record["model"], record.get("user"), record.get("total_tokens"))

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        import asyncio
        await asyncio.to_thread(self.log_success_event, kwargs, response_obj, start_time, end_time)

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------
    def log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        record = self._build_record(kwargs, response_obj, start_time, end_time, success=False)
        _write_log(record)
        logger.warning(
            "request_failed model=%s user=%s client=%s category=%s error_type=%s error=%s",
            record["model"],
            record.get("user"),
            record.get("airlock_client"),
            record.get("failure_category"),
            record.get("error_type"),
            record.get("error"),
        )

    async def async_log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        import asyncio
        await asyncio.to_thread(self.log_failure_event, kwargs, response_obj, start_time, end_time)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_record(
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
        *,
        success: bool,
    ) -> dict[str, Any]:
        metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
        airlock_client = _get_airlock_client(metadata, kwargs)
        error = None
        error_type = None
        failure_category = None
        if not success:
            error, error_type, failure_category = _normalize_failure(kwargs, response_obj)

        # Token usage
        usage: dict[str, int] = {}
        if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
            u = response_obj.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0),
                "completion_tokens": getattr(u, "completion_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }

        # Collect airlock_* guardrail metadata (semantic scores, priority,
        # failover info) so the slow analyzer can see classifier verdicts.
        guardrail_meta = {
            k: v for k, v in metadata.items() if k.startswith("airlock_")
        }

        # MCP tool call metadata
        call_type = kwargs.get("call_type", "")
        litellm_params = kwargs.get("litellm_params", {})
        mcp_meta: dict[str, Any] = {}
        if call_type == "call_mcp_tool" or "mcp_tool_name" in kwargs:
            mcp_meta["call_type"] = call_type or "call_mcp_tool"
            mcp_meta["mcp_tool_name"] = (
                kwargs.get("mcp_tool_name")
                or litellm_params.get("mcp_tool_name")
                or metadata.get("mcp_tool_name")
            )
            mcp_meta["mcp_server_name"] = (
                kwargs.get("mcp_server_name")
                or litellm_params.get("mcp_server_name")
                or metadata.get("mcp_server_name")
            )

        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "success": success,
            "model": kwargs.get("model", "unknown"),
            "user": metadata.get("user_api_key_alias") or metadata.get("user_api_key_user_id"),
            "team": metadata.get("user_api_key_team_alias"),
            "request_id": kwargs.get("litellm_call_id"),
            "messages": kwargs.get("messages"),
            "response": _serialize(response_obj) if response_obj else None,
            "error": error,
            "error_type": error_type,
            "failure_category": failure_category,
            "start_time": start_time,
            "end_time": end_time,
            "duration_ms": (
                int((end_time - start_time).total_seconds() * 1000)
                if start_time and end_time
                else None
            ),
            **usage,
            **mcp_meta,
            **guardrail_meta,
        }
        if airlock_client:
            record["airlock_client"] = airlock_client
        return record


# Module-level instance for config.yaml callback registration.
# LiteLLM's get_instance_fn does getattr — it needs an instance, not a class.
# We also self-register into the async callback lists because the proxy runs
# async but config's success_callback key only populates the sync list.
proxy_logger = AirlockLogger()


def _patch_lowest_cost_none_guard() -> None:
    """Monkey-patch LiteLLM lowest_cost router strategy to guard against None litellm_params.

    LiteLLM's LowestCostLoggingHandler.async_log_success_event does
    ``kwargs["litellm_params"].get(...)`` without guarding against None,
    which crashes for call types (MCP, custom providers) that don't
    fully populate litellm_params.
    """
    try:
        from litellm.router_strategy.lowest_cost import LowestCostLoggingHandler

        _orig = LowestCostLoggingHandler.async_log_success_event

        async def _safe_async_log_success(self, kwargs, response_obj, start_time, end_time):
            if kwargs.get("litellm_params") is None:
                kwargs["litellm_params"] = {}
            return await _orig(self, kwargs, response_obj, start_time, end_time)

        LowestCostLoggingHandler.async_log_success_event = _safe_async_log_success
    except Exception:
        pass


def _self_register() -> None:
    """Ensure proxy_logger is in both sync and async callback lists."""
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_success_callback(proxy_logger)
        mgr.add_litellm_failure_callback(proxy_logger)
        mgr.add_litellm_async_success_callback(proxy_logger)
        mgr.add_litellm_async_failure_callback(proxy_logger)
    except Exception:
        logger.warning("enterprise_logger self-registration deferred — litellm not fully loaded")

    _patch_lowest_cost_none_guard()


_self_register()
