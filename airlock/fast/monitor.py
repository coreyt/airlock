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

import json
import logging
import os
import time
from typing import Any

from airlock.callbacks.metrics import record_provider_ratelimit_headroom
from airlock.client_identity import extract_airlock_client_from_kwargs
from airlock.fast.ratelimit_headers import parse_ratelimit_headers
from airlock.gemini_interface import classify_gemini_response
from airlock.guardrails.extract import is_batch_call
from airlock.transparency import attribute_served_backend, get_transparency_config
from litellm.exceptions import APIError, RateLimitError
from litellm.integrations.custom_logger import CustomLogger

from .state import normalize_client_id, store

logger = logging.getLogger("airlock.fast.monitor")

from .router import infer_provider as _infer_provider

_DEFAULT_BUDGET_WARN_RATIO = 0.8
_budget_warned: set[str] = set()  # warn once per provider per process (anti-spam)
# Explicit per-provider daily caps from provider_budget_config, captured at
# startup. A3 warns ONLY for providers with an explicit cap here or in
# AIRLOCK_PROVIDER_BUDGETS — never the router's internal routing defaults, so a
# deploy that configures no budget gets no warn (CC-3).
_configured_budgets: dict[str, float] = {}


def configure_budgets(config: dict | None) -> None:
    """Capture explicit provider_budget_config caps at startup (CC-2/CC-3)."""
    global _configured_budgets
    budgets: dict[str, float] = {}
    block = (config or {}).get("provider_budget_config") or {}
    if isinstance(block, dict):
        for prov, cfg in block.items():
            limit = cfg.get("budget_limit") if isinstance(cfg, dict) else None
            if isinstance(limit, (int, float)):
                budgets[str(prov)] = float(limit)
    _configured_budgets = budgets


def _budget_warn_ratio() -> float:
    raw = os.getenv("AIRLOCK_BUDGET_WARN_RATIO")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DEFAULT_BUDGET_WARN_RATIO


def _explicit_budget_for(provider: str) -> float | None:
    """Explicit daily cap for a provider, or None if none is configured.

    AIRLOCK_PROVIDER_BUDGETS (env, explicit) overrides the captured
    provider_budget_config. Never falls back to the router's routing defaults.
    """
    raw = os.getenv("AIRLOCK_PROVIDER_BUDGETS")
    if raw:
        try:
            env_budgets = json.loads(raw)
            if isinstance(env_budgets, dict) and provider in env_budgets:
                return float(env_budgets[provider])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return _configured_budgets.get(provider)


def _maybe_warn_budget(provider: str, spend_state, kwargs: dict) -> bool:
    """A3: warn (once) when a provider crosses warn_ratio of its daily cap.

    Sets ``airlock_budget_state=near_limit`` in response metadata and logs once
    per provider per process. Returns whether the provider is near its limit.
    Only fires for providers with an *explicitly configured* budget (CC-3).
    """
    limit = _explicit_budget_for(provider)
    if not limit:
        return False
    spent = spend_state.recent_spend()
    near = spent >= limit * _budget_warn_ratio()
    if near:
        metadata = (kwargs.get("litellm_params", {}) or {}).get("metadata")
        if isinstance(metadata, dict):
            metadata.setdefault("airlock_response_headers", {})[
                "X-Airlock-Budget-State"
            ] = "near_limit"
        if provider not in _budget_warned:
            _budget_warned.add(provider)
            logger.warning(
                "provider_budget_near_limit provider=%s spent=%.2f limit=%.2f",
                provider,
                spent,
                limit,
            )
    elif provider in _budget_warned:
        _budget_warned.discard(provider)  # reset once it drops back under
    return near


def _extract_client_id(kwargs: dict) -> str:
    """Derive a client identifier from LiteLLM callback kwargs.

    Must match guardian._extract_client_id() to ensure the same ClientState
    object is used for pre-call threat/priority and post-call metrics.
    """
    metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
    airlock_client = metadata.get(
        "airlock_client"
    ) or extract_airlock_client_from_kwargs(kwargs)
    if airlock_client:
        return normalize_client_id(airlock_client)
    # Primary: raw API key (same as guardian uses from user_api_key_dict.api_key)
    api_key = metadata.get("user_api_key") or ""
    if len(api_key) > 8:
        return f"key:{api_key[-8:]}"
    # Fallback: user alias or user ID
    user = metadata.get("user_api_key_alias") or metadata.get("user_api_key_user_id")
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

        # Batch/file jobs run async and out-of-band; their latency must not
        # pollute interactive model latency/health/percentile stats. Client-level
        # accounting still happens (consistent with how mcp is handled below).
        is_batch = is_batch_call(kwargs, kwargs.get("call_type", ""))

        store.get_client(client_id).record_success(now, duration_ms)
        if not is_batch:
            store.get_model(model_name).record_success(now, duration_ms)

        # Track spend per provider for budget-aware routing
        cost = kwargs.get("response_cost", 0.0)
        provider = _infer_provider(model_name)
        # CC-T4: key spend (and the other per-provider success counters) off the
        # SERVED backend, not the inferred one — a same-provider failover or backend
        # swap is then billed correctly. Flag-gated; falls back to inferred when the
        # served read is absent/unparseable (a monitor callback must never crash the
        # request path). response_obj is the final served response.
        if get_transparency_config().attribute_accounting_to_served:
            try:
                served = attribute_served_backend(
                    response_obj, cost_fallback=kwargs.get("response_cost")
                )
            except Exception:
                logger.warning(
                    "served-backend attribution failed; billing falls back to inferred provider",
                    exc_info=True,
                )
                served = None
            if served and served.provider:
                provider = served.provider
                cost = (
                    served.response_cost
                    if served.response_cost is not None
                    else kwargs.get("response_cost", 0.0)
                )
        if provider:
            if cost and cost > 0:
                store.get_provider_spend(provider).record_spend(now, cost)
                # A3: warn (once) when crossing the daily-budget warn ratio.
                _maybe_warn_budget(provider, store.get_provider_spend(provider), kwargs)
            store.record_provider_request(client_id, provider, now)
            store.record_provider_success(client_id, provider, now)
            # Capture upstream quota headroom (workstream C, observe-only).
            hidden = getattr(response_obj, "_hidden_params", None)
            if isinstance(hidden, dict):
                parsed = parse_ratelimit_headers(hidden.get("additional_headers") or {})
                store.record_provider_ratelimit(provider, parsed, now)
                record_provider_ratelimit_headroom(
                    provider,
                    parsed["remaining_tokens"],
                    parsed["remaining_requests"],
                )
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
        is_mcp = kwargs.get("call_type") == "call_mcp_tool" or "mcp_tool_name" in kwargs
        store.record_call_type(is_mcp)
        if is_mcp:
            tool_name = kwargs.get("mcp_tool_name", "unknown")
            server_name = kwargs.get("mcp_server_name", "")
            store.get_mcp_tool(tool_name, server_name).record_success(
                now,
                duration_ms,
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

        # Pre-call failures (auth, blocked, no-db, etc.) arrive with
        # exception=None because the proxy rejected the request before
        # LiteLLM reached the model.  They must not affect the circuit
        # breaker or provider-quarantine state — those track model/provider
        # health, not client configuration errors.
        is_provider_call = exception is not None
        # Batch/file jobs must not feed model circuit-breaker health (mirrors
        # the success-path exclusion above).
        is_batch = is_batch_call(kwargs, kwargs.get("call_type", ""))

        store.get_client(client_id).record_error(now, error_type)
        if is_provider_call and not is_batch:
            store.get_model(model_name).record_failure(now)
        provider = _infer_provider(model_name)
        if provider:
            store.record_provider_request(client_id, provider, now)
            if is_provider_call:
                store.record_provider_failure(client_id, provider, now)

        # CC-T4: the quarantine/rate-limit counter keys off the provider parsed from
        # the error (the backend that actually 429'd), else the inferred provider —
        # never a served read (often no response on failure). Attempt/breaker counting
        # above stays per-attempted-provider (inferred). Flag-gated.
        if get_transparency_config().attribute_accounting_to_served:
            provider = getattr(exception, "llm_provider", None) or provider

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
            # Capture the exhausted-quota snapshot from the 429 response headers.
            resp = getattr(exception, "response", None)
            resp_headers = getattr(resp, "headers", None)
            if resp_headers is not None:
                parsed = parse_ratelimit_headers(resp_headers)
                store.record_provider_ratelimit(provider, parsed, now)
                record_provider_ratelimit_headroom(
                    provider,
                    parsed["remaining_tokens"],
                    parsed["remaining_requests"],
                )
            metadata = litellm_params.get("metadata") or {}
            litellm_params["metadata"] = metadata
            # Three-way label so a below-threshold 429 (nothing armed, possible
            # when rate_limit_threshold > 1) is NOT logged as a quarantine —
            # otherwise ingest_jsonl_record would re-quarantine the TUI replica.
            if outcome["provider_quarantined"]:
                action = "provider_quarantine"
                cooldown = outcome["provider_cooldown_seconds"]
            elif outcome["client_quarantined"]:
                action = "client_quarantine"
                cooldown = outcome["client_cooldown_seconds"]
            else:
                action = "rate_limited"
                cooldown = 0.0
            metadata["airlock_provider"] = provider
            metadata["airlock_provider_protection"] = {
                "action": action,
                "scope": "provider"
                if outcome["provider_quarantined"]
                else "client_provider",
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
        is_mcp = kwargs.get("call_type") == "call_mcp_tool" or "mcp_tool_name" in kwargs
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
