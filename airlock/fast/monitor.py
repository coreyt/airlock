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

from airlock.client_identity import extract_airlock_client_from_kwargs
from airlock.gemini_interface import classify_gemini_response
from litellm.exceptions import APIError, RateLimitError
from litellm.integrations.custom_logger import CustomLogger

from .state import normalize_client_id, store

logger = logging.getLogger("airlock.fast.monitor")

from .router import infer_provider as _infer_provider


def _extract_client_id(kwargs: dict) -> str:
    """Derive a client identifier from LiteLLM callback kwargs.

    Must match guardian._extract_client_id() to ensure the same ClientState
    object is used for pre-call threat/priority and post-call metrics.
    """
    metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
    airlock_client = (
        metadata.get("airlock_client")
        or extract_airlock_client_from_kwargs(kwargs)
    )
    if airlock_client:
        return normalize_client_id(airlock_client)
    # Primary: raw API key (same as guardian uses from user_api_key_dict.api_key)
    api_key = metadata.get("user_api_key") or ""
    if len(api_key) > 8:
        return f"key:{api_key[-8:]}"
    # Fallback: user alias or user ID
    user = (
        metadata.get("user_api_key_alias")
        or metadata.get("user_api_key_user_id")
    )
    if user:
        return f"user:{user}"
    return normalize_client_id(None)


def _is_provider_rate_limited(exc: Exception | None) -> tuple[bool, str]:
    """Detect provider 429/quota exhaustion signals."""
    if exc is None:
        return False, ""
    text = str(exc).strip()
    lowered = text.lower()
    if isinstance(exc, RateLimitError):
        return True, text or "provider_rate_limited"
    if isinstance(exc, APIError) and getattr(exc, "status_code", None) == 429:
        return True, text or "provider_rate_limited"
    markers = (
        "rate limit",
        "too many requests",
        "exceeded your current quota",
        "insufficient_quota",
        "quota",
    )
    if any(marker in lowered for marker in markers):
        return True, text or "provider_rate_limited"
    return False, text


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
        provider = _infer_provider(model_name)
        if provider:
            if cost and cost > 0:
                store.get_provider_spend(provider).record_spend(now, cost)
            store.record_provider_request(client_id, provider, now)
            store.record_provider_success(client_id, provider, now)
            if provider == "gemini":
                metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
                gemini_request = metadata.get("airlock_gemini") or {}
                gemini_response = classify_gemini_response(response_obj) or {}
                if gemini_response:
                    store.record_gemini_outcome(
                        client_id,
                        provider,
                        now,
                        str(gemini_response.get("output_shape") or "unknown"),
                        str(gemini_request.get("mode") or "balanced"),
                    )

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
        exception = kwargs.get("exception")
        error_type = type(exception).__name__

        store.get_client(client_id).record_error(now, error_type)
        store.get_model(model_name).record_failure(now)
        provider = _infer_provider(model_name)
        if provider:
            store.record_provider_request(client_id, provider, now)
            store.record_provider_failure(client_id, provider, now)

        is_rate_limited, reason = _is_provider_rate_limited(exception)
        if provider and is_rate_limited:
            litellm_params = kwargs.get("litellm_params")
            if not isinstance(litellm_params, dict):
                litellm_params = {}
                kwargs["litellm_params"] = litellm_params
            outcome = store.record_provider_rate_limit(
                client_id,
                provider,
                now,
                reason or "provider_rate_limited",
                error_type or "RateLimitError",
            )
            metadata = litellm_params.setdefault("metadata", {})
            action = "provider_quarantine" if outcome["provider_quarantined"] else "client_quarantine"
            cooldown = (
                outcome["provider_cooldown_seconds"]
                if outcome["provider_quarantined"]
                else outcome["client_cooldown_seconds"]
            )
            metadata["airlock_provider"] = provider
            metadata["airlock_provider_protection"] = {
                "action": action,
                "scope": "provider" if outcome["provider_quarantined"] else "client_provider",
                "client_id": client_id,
                "provider": provider,
                "requested_model": model_name,
                "final_model": model_name,
                "reason": reason or "provider_rate_limited",
                "cooldown_seconds": round(float(cooldown), 1),
                "impacted_clients": int(outcome["impacted_clients"]),
            }
            logger.warning(
                "provider_protection action=%s client=%s provider=%s model=%s cooldown=%.0fs impacted_clients=%s reason=%s",
                action,
                client_id,
                provider,
                model_name,
                float(cooldown),
                int(outcome["impacted_clients"]),
                reason or "provider_rate_limited",
            )

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
        logger.warning("monitor self-registration deferred — litellm not fully loaded")


_self_register()
