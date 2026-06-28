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


def _record_mutations_from_event(mutations: list) -> None:
    """Increment mutations_total per asdict'd ledger mutation (field/op labels).

    ``event.mutations`` is the ``dataclasses.asdict``'d form (list of dicts with
    ``field``/``op`` keys), so this reads via dict access — behavior-identical to the
    old ``getattr``-based ``_record_mutations`` (same field/op values, same skip rule
    when either is None, same no-crash on a non-iterable).
    """
    if "mutations_total" not in _metrics:
        return
    try:
        iterator = iter(mutations)
    except TypeError:
        return
    for m in iterator:
        # event.mutations is normally the asdict'd list of dicts, but _serialize's
        # str() fallback could yield a non-dict — skip it gracefully (the old
        # getattr-based version was safe for any item type; preserve that parity).
        if not isinstance(m, dict):
            continue
        field = m.get("field")
        op = m.get("op")
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
    """Records the per-request Prometheus metrics from a ``RequestEvent``.

    Driven via the recorder seam (``record_event``) rather than LiteLLM callbacks:
    ``requests_total`` fires on BOTH success and failure; ``request_duration`` and
    the mutation counter fire on success only — identical to the old success/failure
    callbacks.
    """

    def record_event(self, event: Any) -> None:
        if not _PROM_AVAILABLE:
            return

        model = event.model
        user = event.user or "unknown"
        _metrics["requests_total"].labels(
            model=model, user=user, success="true" if event.success else "false"
        ).inc()

        if event.success:
            if event.start_time and event.end_time:
                duration_s = (event.end_time - event.start_time).total_seconds()
                _metrics["request_duration"].labels(model=model).observe(duration_s)
            _record_mutations_from_event(event.mutations)


# Module-level instance registered as an unconditional recorder sink (see
# airlock.callbacks.recorder). metrics is always-on; its per-request counters are
# dispatched through the recorder, not self-registered into LiteLLM's callback lists.
metrics_callback = AirlockMetricsCallback()
