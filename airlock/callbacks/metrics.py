"""
Airlock Metrics — Prometheus metrics callback for LiteLLM.

Exposes counters and histograms for request volume, latency, PII
redactions, keyword blocks, circuit breaker state, and threat blocks.

Requires: pip install airlock-llm[metrics]
    (prometheus-client>=0.20.0)

The metrics are exposed via the default Prometheus registry and can be
scraped at /metrics when running behind a WSGI/ASGI middleware, or
collected by the Prometheus push gateway.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("airlock.callbacks.metrics")

try:
    from prometheus_client import Counter, Gauge, Histogram

    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False

from litellm.integrations.custom_logger import CustomLogger


def _build_metrics() -> dict[str, Any]:
    """Create and return all Prometheus metric objects."""
    if not _PROM_AVAILABLE:
        return {}

    return {
        "requests_total": Counter(
            "airlock_requests_total",
            "Total LLM requests proxied by Airlock",
            ["model", "user", "success"],
        ),
        "request_duration": Histogram(
            "airlock_request_duration_seconds",
            "LLM request duration in seconds",
            ["model"],
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
        ),
        "pii_redactions": Counter(
            "airlock_pii_redactions_total",
            "Total PII entities redacted",
            ["entity_type"],
        ),
        "keyword_blocks": Counter(
            "airlock_keyword_blocks_total",
            "Total requests blocked by keyword guard",
        ),
        "circuit_breaker_state": Gauge(
            "airlock_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=half_open, 2=open)",
            ["model"],
        ),
        "provider_ratelimit_remaining_tokens": Gauge(
            "airlock_provider_ratelimit_remaining_tokens",
            "Upstream remaining-tokens headroom per provider (latest observed)",
            ["provider"],
        ),
        "provider_ratelimit_remaining_requests": Gauge(
            "airlock_provider_ratelimit_remaining_requests",
            "Upstream remaining-requests headroom per provider (latest observed)",
            ["provider"],
        ),
        "threat_blocks": Counter(
            "airlock_threat_blocks_total",
            "Total requests blocked by threat detector",
        ),
        "response_scan_detections": Counter(
            "airlock_response_scan_detections_total",
            "Total response scan detections",
            ["category", "mode"],
        ),
        "mutations_total": Counter(
            "airlock_mutations_total",
            "Total ledger mutations by field and operation",
            ["field", "op"],
        ),
    }


# Module-level metrics (created once at import)
_metrics = _build_metrics()


# ---------------------------------------------------------------------------
# Public helpers for guardrails to call
# ---------------------------------------------------------------------------
def record_pii_redaction(entity_type: str) -> None:
    """Increment PII redaction counter. Called by pii_guard."""
    if "pii_redactions" in _metrics:
        _metrics["pii_redactions"].labels(entity_type=entity_type).inc()


def record_keyword_block() -> None:
    """Increment keyword block counter. Called by keyword_guard."""
    if "keyword_blocks" in _metrics:
        _metrics["keyword_blocks"].inc()


def record_provider_ratelimit_headroom(
    provider: str,
    remaining_tokens: int | None,
    remaining_requests: int | None,
) -> None:
    """Set the latest upstream headroom gauges for a provider (workstream C)."""
    if (
        remaining_tokens is not None
        and "provider_ratelimit_remaining_tokens" in _metrics
    ):
        _metrics["provider_ratelimit_remaining_tokens"].labels(provider=provider).set(
            remaining_tokens
        )
    if (
        remaining_requests is not None
        and "provider_ratelimit_remaining_requests" in _metrics
    ):
        _metrics["provider_ratelimit_remaining_requests"].labels(provider=provider).set(
            remaining_requests
        )


def record_response_scan_detection(category: str, mode: str) -> None:
    """Increment response scan detection counter. Called by response_scanner."""
    if "response_scan_detections" in _metrics:
        _metrics["response_scan_detections"].labels(category=category, mode=mode).inc()


def record_threat_block() -> None:
    """Increment threat block counter. Called by fast guardian."""
    if "threat_blocks" in _metrics:
        _metrics["threat_blocks"].inc()


def _record_mutations(metadata: dict) -> None:
    """Increment mutations_total per ledger Mutation (field/op bounded labels)."""
    if "mutations_total" not in _metrics:
        return
    ledger = metadata.get("airlock_mutations") or []
    try:
        iterator = iter(ledger)
    except TypeError:
        return
    for m in iterator:
        field = getattr(m, "field", None)
        op = getattr(m, "op", None)
        if field is None or op is None:
            continue
        _metrics["mutations_total"].labels(field=field, op=op).inc()


def set_circuit_breaker_state(model: str, state: str) -> None:
    """Set circuit breaker gauge. Called by circuit_breaker."""
    if "circuit_breaker_state" in _metrics:
        state_map = {"closed": 0, "half_open": 1, "open": 2}
        _metrics["circuit_breaker_state"].labels(model=model).set(
            state_map.get(state, -1)
        )


# ---------------------------------------------------------------------------
# LiteLLM callback
# ---------------------------------------------------------------------------
class AirlockMetricsCallback(CustomLogger):
    """LiteLLM callback that records Prometheus metrics."""

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        if not _PROM_AVAILABLE:
            return

        metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
        model = kwargs.get("model", "unknown")
        user = (
            metadata.get("user_api_key_alias")
            or metadata.get("user_api_key_user_id")
            or "unknown"
        )

        _metrics["requests_total"].labels(model=model, user=user, success="true").inc()

        if start_time and end_time:
            duration_s = (end_time - start_time).total_seconds()
            _metrics["request_duration"].labels(model=model).observe(duration_s)

        _record_mutations(metadata)

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    def log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        if not _PROM_AVAILABLE:
            return

        metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
        model = kwargs.get("model", "unknown")
        user = (
            metadata.get("user_api_key_alias")
            or metadata.get("user_api_key_user_id")
            or "unknown"
        )

        _metrics["requests_total"].labels(model=model, user=user, success="false").inc()

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)


# Module-level instance for config.yaml callback registration.
# LiteLLM's get_instance_fn does getattr — it needs an instance, not a class.
# We also self-register into the async callback lists because the proxy runs
# async but config's success_callback key only populates the sync list.
metrics_callback = AirlockMetricsCallback()


def _self_register() -> None:
    """Ensure metrics_callback is in both sync and async callback lists.

    LiteLLM's logging_callback_manager dedupes, so repeat calls are idempotent.
    """
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_success_callback(metrics_callback)
        mgr.add_litellm_failure_callback(metrics_callback)
        mgr.add_litellm_async_success_callback(metrics_callback)
        mgr.add_litellm_async_failure_callback(metrics_callback)
    except Exception:
        logger.warning("metrics self-registration deferred — litellm not fully loaded")


_self_register()
