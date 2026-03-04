"""
Airlock Fast Monitor — LiteLLM callback that feeds metrics back into
the fast subsystem's in-memory state store.

Runs on every success and failure to update:
  - Client latency / error tracking  (drives priority scoring)
  - Model health tracking            (drives circuit breaker)

Registered in config.yaml alongside the enterprise logger:

    litellm_settings:
        success_callback: [..., "airlock.fast.monitor"]
        failure_callback: [..., "airlock.fast.monitor"]
"""

from __future__ import annotations

import logging
import time
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from .state import store

logger = logging.getLogger("airlock.fast.monitor")

# Provider inference for spend tracking — prefix heuristic.
_PROVIDER_PREFIXES = {
    "claude": "anthropic",
    "gpt": "openai",
    "gemini": "gemini",
    "mistral": "mistral",
    "codestral": "mistral",
    "magistral": "mistral",
}


def _infer_provider(model_name: str) -> str | None:
    """Map a model alias to its provider name via prefix matching."""
    for prefix, provider in _PROVIDER_PREFIXES.items():
        if model_name.startswith(prefix):
            return provider
    return None


def _extract_client_id(kwargs: dict) -> str:
    """Derive a client identifier from LiteLLM callback kwargs."""
    metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
    user = (
        metadata.get("user_api_key_alias")
        or metadata.get("user_api_key_user_id")
    )
    if user:
        return f"user:{user}"
    api_key = metadata.get("user_api_key")
    if api_key and len(api_key) > 8:
        return f"key:{api_key[-8:]}"
    return "unknown"


class AirlockFastMonitor(CustomLogger):
    """Callback that updates the fast subsystem's state on every request."""

    # ------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------
    def log_success_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        now = time.time()
        client_id = _extract_client_id(kwargs)
        model_name = kwargs.get("model", "unknown")

        duration_ms = (
            (end_time - start_time).total_seconds() * 1000
            if start_time and end_time
            else 0.0
        )

        store.get_client(client_id).record_success(now, duration_ms)
        store.get_model(model_name).record_success(now, duration_ms)

        # Track spend per provider for budget-aware routing
        cost = kwargs.get("response_cost", 0.0)
        if cost and cost > 0:
            provider = _infer_provider(model_name)
            if provider:
                store.get_provider_spend(provider).record_spend(now, cost)

        # Track MCP tool state and traffic split
        is_mcp = (
            kwargs.get("call_type") == "call_mcp_tool"
            or "mcp_tool_name" in kwargs
        )
        store.record_call_type(is_mcp)
        if is_mcp:
            tool_name = kwargs.get("mcp_tool_name", "unknown")
            server_name = kwargs.get("mcp_server_name", "")
            store.get_mcp_tool(tool_name, server_name).record_success(
                now, duration_ms,
            )

        logger.debug(
            "monitor_success client=%s model=%s latency=%.0fms",
            client_id,
            model_name,
            duration_ms,
        )

    async def async_log_success_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------
    def log_failure_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        now = time.time()
        client_id = _extract_client_id(kwargs)
        model_name = kwargs.get("model", "unknown")
        error_type = type(kwargs.get("exception", Exception())).__name__

        store.get_client(client_id).record_error(now, error_type)
        store.get_model(model_name).record_failure(now)

        # Track MCP tool state and traffic split
        is_mcp = (
            kwargs.get("call_type") == "call_mcp_tool"
            or "mcp_tool_name" in kwargs
        )
        store.record_call_type(is_mcp)
        if is_mcp:
            tool_name = kwargs.get("mcp_tool_name", "unknown")
            server_name = kwargs.get("mcp_server_name", "")
            store.get_mcp_tool(tool_name, server_name).record_failure(now)

        logger.debug(
            "monitor_failure client=%s model=%s error=%s",
            client_id,
            model_name,
            error_type,
        )

    async def async_log_failure_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)


# Module-level instance for config.yaml callback registration.
# LiteLLM's get_instance_fn does getattr — it needs an instance, not a class.
# We also self-register into the async callback lists because the proxy runs
# async but config's success_callback key only populates the sync list.
proxy_monitor = AirlockFastMonitor()


def _self_register() -> None:
    """Ensure proxy_monitor is in both sync and async callback lists."""
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_success_callback(proxy_monitor)
        mgr.add_litellm_failure_callback(proxy_monitor)
        mgr.add_litellm_async_success_callback(proxy_monitor)
        mgr.add_litellm_async_failure_callback(proxy_monitor)
    except Exception:
        pass  # litellm not fully loaded yet — config path will handle it


_self_register()
