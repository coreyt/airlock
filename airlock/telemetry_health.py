"""Bounded, secret-safe instrumentation status for operator diagnostics.

This is deliberately not an exporter implementation.  It distinguishes a
Prometheus registry (pull, so no downstream delivery can be claimed) from an
actual tracing exporter.  Callback instrumentation is fail-open.
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

_LOCK = threading.RLock()
_MAX_ERROR = 80
_health: dict[str, dict] = {}


def _safe_endpoint(value: str | None) -> str | None:
    """Keep only scheme and authority; never publish credential/query/path."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        return None
    if port:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def configure_exporter(
    name: str, *, enabled: bool, endpoint: str | None = None
) -> None:
    """Declare an instrumentation/exporter state without treating it as delivery."""
    with _LOCK:
        item = _health.setdefault(name, {})
        item.update(
            {
                "enabled": bool(enabled),
                "endpoint": _safe_endpoint(endpoint),
                "updated_at": time.time(),
            }
        )
        item.setdefault("successes", 0)
        item.setdefault("failures", 0)
        item.setdefault("last_success_at", None)
        item.setdefault("last_error", None)


def record_signal(name: str) -> None:
    """Record local instrumentation success; only exporters may claim delivery."""
    with _LOCK:
        item = _health.setdefault(name, {})
        item["signals"] = int(item.get("signals", 0)) + 1
        item["updated_at"] = time.time()


def record_export_success(name: str) -> None:
    with _LOCK:
        item = _health.setdefault(name, {})
        item["successes"] = int(item.get("successes", 0)) + 1
        item["last_success_at"] = time.time()
        item["updated_at"] = time.time()


def record_export_failure(name: str, error: Exception | str) -> None:
    """Retain only a bounded exception category, never provider response text."""
    # A string commonly is ``str(exc)`` and can contain an upstream body,
    # credentials, or prompt content. Treat it as an untrusted unknown class,
    # never as a displayable message.
    category = "export_error" if isinstance(error, str) else type(error).__name__
    category = str(category).replace("\n", " ")[:_MAX_ERROR]
    with _LOCK:
        item = _health.setdefault(name, {})
        item["failures"] = int(item.get("failures", 0)) + 1
        item["last_error"] = category
        item["updated_at"] = time.time()


def configure_from_environment(
    *, metrics_available: bool, tracing_available: bool
) -> None:
    """Describe built-in telemetry without inventing an external exporter."""
    configure_exporter(
        "prometheus",
        enabled=metrics_available,
        endpoint="http://in-process/metrics" if metrics_available else None,
    )
    configure_exporter(
        "otlp",
        enabled=tracing_available,
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )


def telemetry_snapshot() -> dict[str, dict]:
    """Return a copy suitable for an authenticated operator read."""
    with _LOCK:
        return {name: dict(value) for name, value in _health.items()}
