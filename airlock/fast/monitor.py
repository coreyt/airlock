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
proxy_monitor = AirlockFastMonitor()
