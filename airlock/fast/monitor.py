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
import os
import time
from typing import Any

from airlock.callbacks.metrics import record_provider_ratelimit_headroom
from airlock.client_identity import extract_airlock_client_from_kwargs
from airlock.fast.ratelimit_headers import parse_ratelimit_headers
from airlock.gemini_interface import classify_gemini_response
from airlock.guardrails.extract import is_batch_call
from airlock.litellm_adapter import additional_headers, hidden_params
from airlock.transparency import attribute_served_backend, get_transparency_config
from litellm.exceptions import APIError, RateLimitError
from litellm.integrations.custom_logger import CustomLogger

from .settings import get_settings
from .state import normalize_client_id, store

logger = logging.getLogger("airlock.fast.monitor")

from .router import infer_provider as _infer_provider

_budget_warned: set[str] = set()  # warn once per provider per process (anti-spam)


def _explicit_budget_for(provider: str) -> float | None:
    """Explicit daily cap for a provider, or None if none is configured.

    Sourced from ``get_settings().provider_budgets`` (R6 fix: reads the
    ``router_settings.provider_budget_config`` nesting; the ``AIRLOCK_PROVIDER_BUDGETS``
    env override is handled inside ``get_settings``). There is no hidden default — an
    unconfigured provider returns ``None`` (no warn).
    """
    return get_settings().provider_budgets.get(provider)


def _maybe_warn_budget(provider: str, spend_state, kwargs: dict) -> bool:
    """A3: warn (once) when a provider crosses warn_ratio of its daily cap.

    Sets ``airlock_budget_state=near_limit`` in response metadata and logs once
    per provider per process. Returns whether the provider is near its limit.
    Only fires for providers with an *explicitly configured* budget (CC-3); a ``0``
    or absent budget short-circuits to no-warn (AC-0).
    """
    limit = _explicit_budget_for(provider)
    if not limit:
        return False
    spent = spend_state.recent_spend()
    near = spent >= limit * get_settings().budget_warn_ratio
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
            hidden = hidden_params(response_obj)
            if isinstance(hidden, dict):
                parsed = parse_ratelimit_headers(additional_headers(response_obj) or {})
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


# ---------------------------------------------------------------------------
# FIX-1 — checkpoint/restore run in the litellm CHILD process, where spend is
# actually mutated. The monitor module is imported in BOTH the launcher (for
# configure_budgets) and the child (as the callback); the AIRLOCK_LITELLM_CHILD
# env var (set by proxy.py on the subprocess env) gates this glue to the child
# only, so the launcher is no longer a second writer.
# ---------------------------------------------------------------------------
_DEFAULT_CHECKPOINT_INTERVAL = 60.0  # seconds between periodic child checkpoints
_checkpoint_stop = None  # type: ignore[var-annotated]


def _state_paths() -> tuple[str, str]:
    state_dir = os.getenv("AIRLOCK_STATE_DIR", os.getenv("AIRLOCK_LOG_DIR", "./logs"))
    return (
        os.path.join(state_dir, "cb_state.json"),
        os.path.join(state_dir, "spend_state.json"),
    )


def _checkpoint_child_state() -> None:
    """Persist breaker + spend state from the child (best-effort)."""
    from .state import checkpoint_spend, checkpoint_state

    cb_path, spend_path = _state_paths()
    try:
        checkpoint_state(store, cb_path)
    except Exception:
        logger.warning("breaker checkpoint failed", exc_info=True)
    try:
        checkpoint_spend(store, spend_path)
    except Exception:
        logger.warning("spend checkpoint failed", exc_info=True)


def _restore_child_state() -> None:
    """Rehydrate breaker (5-min gated) + spend (age-bounded) on child startup."""
    from .state import restore_spend, restore_state

    cb_path, spend_path = _state_paths()
    try:
        restore_state(store, cb_path)
    except Exception:
        logger.warning("breaker restore failed", exc_info=True)
    try:
        restore_spend(store, spend_path)
    except Exception:
        logger.warning("spend restore failed", exc_info=True)


def _checkpoint_interval() -> float:
    raw = os.getenv("AIRLOCK_SPEND_CHECKPOINT_INTERVAL")
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_CHECKPOINT_INTERVAL


def _init_child_state() -> None:
    """Wire FIX-1 in the litellm child: restore-on-start + periodic/shutdown save."""
    import atexit
    import signal
    import threading

    global _checkpoint_stop

    _restore_child_state()

    # Periodic checkpoint so durability does not depend on the shutdown signal
    # actually reaching the (possibly orphaned) child.
    _checkpoint_stop = threading.Event()
    interval = _checkpoint_interval()

    def _loop() -> None:
        while not _checkpoint_stop.wait(interval):
            _checkpoint_child_state()

    threading.Thread(target=_loop, name="airlock-spend-checkpoint", daemon=True).start()

    # Shutdown checkpoint: atexit (normal exit) + a SIGTERM handler that chains to
    # any previously installed handler so we do not break litellm/uvicorn shutdown.
    atexit.register(_checkpoint_child_state)

    _prev_handler = signal.getsignal(signal.SIGTERM)

    def _on_sigterm(signum, frame):
        if _checkpoint_stop is not None:
            _checkpoint_stop.set()
        _checkpoint_child_state()
        if callable(_prev_handler):
            _prev_handler(signum, frame)
        elif _prev_handler == signal.SIG_DFL:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTERM)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        # signal.signal only works in the main thread; if the monitor imports off
        # the main thread, the periodic + atexit paths still guarantee durability.
        logger.debug("SIGTERM handler not installed (monitor imported off main thread)")


_self_register()

if os.getenv("AIRLOCK_LITELLM_CHILD") == "1":
    _init_child_state()
