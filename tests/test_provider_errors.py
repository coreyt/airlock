"""Slice 40 provider-error boundary tests."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest
from litellm.exceptions import APIError

from airlock.callbacks.projections import project_enterprise, project_s3, project_sql
from airlock.callbacks.request_event import build_request_event
from airlock.callbacks.tracing import AirlockTracingCallback
from airlock.fast.monitor import _is_provider_rate_limited
from airlock.provider_errors import summarize_provider_error


def _provider_failure(message: str, status: int) -> APIError:
    """Use LiteLLM's actual provider-error shape, not an Airlock test double."""
    return APIError(status, message, "openrouter", "openai/gpt-4o-mini")


def _kwargs(exc: Exception) -> dict:
    return {
        "model": "openrouter/openai/gpt-4o-mini",
        "exception": exc,
        "messages": [{"role": "user", "content": "sentinel-request"}],
        "litellm_params": {
            "metadata": {
                "airlock_provider": "openrouter",
                "user_api_key_alias": "sentinel-user",
            }
        },
    }


@pytest.mark.parametrize("status", [401, 402, 429, 500, 503])
def test_provider_failure_is_bounded_before_request_event_and_projections(status):
    secret = "sentinel-provider-response-and-key"
    event = build_request_event(
        _kwargs(_provider_failure(secret, status)),
        None,
        datetime.datetime.now(datetime.timezone.utc),
        datetime.datetime.now(datetime.timezone.utc),
        success=False,
    )

    assert (
        event.error
        == f"provider_error type=APIError provider=openrouter status={status}"
    )
    assert event.bare_exception_error == event.error
    for projection in (
        project_enterprise(event),
        project_s3(event),
        project_sql(event),
    ):
        assert secret not in repr(projection)
        assert projection["error"] == event.error


def test_provider_429_keeps_typed_detection_without_text_leakage():
    exc = _provider_failure("rate limit sentinel quota exhausted", 429)
    limited, reason = _is_provider_rate_limited(exc)
    assert limited is True
    assert reason == "provider_error type=APIError provider=openrouter status=429"
    assert "sentinel" not in reason


def test_provider_error_summary_rejects_unattributed_local_error():
    assert summarize_provider_error(ValueError("sentinel local error")) is None


def test_provider_error_summary_bounds_unknown_provider_marker():
    summary = summarize_provider_error(
        APIError(500, "harmless", "sentinel_provider_error_payload_ABC123", "x")
    )
    assert summary is not None
    assert summary.provider is None
    assert "sentinel" not in summary.message()


def test_tracing_exports_bounded_provider_exception(monkeypatch):
    from airlock.callbacks import tracing as tracing_module

    span = MagicMock()
    tracer = MagicMock()
    tracer.start_as_current_span.return_value.__enter__ = lambda _: span
    tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(tracing_module, "_tracer", tracer)
    monkeypatch.setattr(tracing_module, "_OTEL_AVAILABLE", True)

    AirlockTracingCallback().log_failure_event(
        _kwargs(_provider_failure("sentinel upstream body", 503)), None, None, None
    )
    attrs = {call.args[0]: call.args[1] for call in span.set_attribute.call_args_list}
    assert (
        attrs["llm.error"]
        == "provider_error type=APIError provider=openrouter status=503"
    )
    recorded = span.record_exception.call_args.args[0]
    assert "sentinel" not in str(recorded)
