"""
Airlock Metrics — Prometheus metrics callback for LiteLLM.

Exposes counters and histograms for request volume, latency, PII
redactions, keyword blocks, circuit breaker state, and threat blocks.

Requires: pip install airlock[metrics]
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
        "threat_blocks": Counter(
            "airlock_threat_blocks_total",
            "Total requests blocked by threat detector",
        ),
        "response_scan_detections": Counter(
            "airlock_response_scan_detections_total",
            "Total response scan detections",
            ["category", "mode"],
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


def record_response_scan_detection(category: str, mode: str) -> None:
    """Increment response scan detection counter. Called by response_scanner."""
    if "response_scan_detections" in _metrics:
        _metrics["response_scan_detections"].labels(category=category, mode=mode).inc()


def record_threat_block() -> None:
    """Increment threat block counter. Called by fast guardian."""
    if "threat_blocks" in _metrics:
        _metrics["threat_blocks"].inc()


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
