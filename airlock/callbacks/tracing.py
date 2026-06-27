"""
Airlock Tracing — OpenTelemetry trace context propagation for LiteLLM.

Creates spans for each guardrail execution and upstream LLM call,
enabling distributed tracing across the proxy pipeline.

Requires: pip install airlock-llm[tracing]
    (opentelemetry-api>=1.20.0, opentelemetry-sdk>=1.20.0)

Env vars:
    AIRLOCK_OTEL_SERVICE_NAME — service name (default: "airlock")
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("airlock.callbacks.tracing")

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.resources import Resource

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

from litellm.integrations.custom_logger import CustomLogger


def _get_tracer() -> Any:
    """Get or create the Airlock tracer."""
    if not _OTEL_AVAILABLE:
        return None

    service_name = os.getenv("AIRLOCK_OTEL_SERVICE_NAME", "airlock")

    # Only set up provider if none exists
    current_provider = trace.get_tracer_provider()
    if isinstance(current_provider, trace.ProxyTracerProvider):
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)

    return trace.get_tracer("airlock", "0.5.1")


_tracer = _get_tracer()


class AirlockTracingCallback(CustomLogger):
    """LiteLLM callback that creates OpenTelemetry spans for each request."""

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        if not _OTEL_AVAILABLE or _tracer is None:
            return

        metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
        model = kwargs.get("model", "unknown")

        with _tracer.start_as_current_span("llm.request") as span:
            span.set_attribute("llm.model", model)
            span.set_attribute("llm.success", True)
            span.set_attribute("llm.request_id", kwargs.get("litellm_call_id", ""))

            user = metadata.get("user_api_key_alias") or metadata.get(
                "user_api_key_user_id"
            )
            if user:
                span.set_attribute("llm.user", user)

            if start_time and end_time:
                duration_ms = int((end_time - start_time).total_seconds() * 1000)
                span.set_attribute("llm.duration_ms", duration_ms)

            if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
                span.set_attribute(
                    "llm.tokens.total",
                    getattr(response_obj.usage, "total_tokens", 0),
                )

            # Record guardrail metadata
            if "airlock_priority" in metadata:
                span.set_attribute(
                    "airlock.priority.score",
                    metadata["airlock_priority"].get("score", 0),
                )
                span.set_attribute(
                    "airlock.priority.boost",
                    metadata["airlock_priority"].get("boost", False),
                )

            if "airlock_failover" in metadata:
                span.set_attribute(
                    "airlock.failover.original",
                    metadata["airlock_failover"].get("original_model", ""),
                )
                span.set_attribute(
                    "airlock.failover.target",
                    metadata["airlock_failover"].get("failover_model", ""),
                )

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    def log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        if not _OTEL_AVAILABLE or _tracer is None:
            return

        model = kwargs.get("model", "unknown")
        error = kwargs.get("exception")

        with _tracer.start_as_current_span("llm.request") as span:
            span.set_attribute("llm.model", model)
            span.set_attribute("llm.success", False)
            span.set_attribute("llm.request_id", kwargs.get("litellm_call_id", ""))

            if error:
                span.set_attribute("llm.error", str(error))
                span.record_exception(error)

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)
