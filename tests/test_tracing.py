"""Tests for airlock.callbacks.tracing — OpenTelemetry tracing callback."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from airlock.callbacks import tracing as tracing_module
from airlock.callbacks.tracing import AirlockTracingCallback


class TestAirlockTracingCallbackSuccess:
    def test_creates_span_with_attributes(self, monkeypatch):
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s: mock_span
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(tracing_module, "_tracer", mock_tracer)
        monkeypatch.setattr(tracing_module, "_OTEL_AVAILABLE", True)

        cb = AirlockTracingCallback()
        start = datetime(2024, 1, 1, 0, 0, 0)
        end = start + timedelta(seconds=1, milliseconds=500)
        kwargs = {
            "model": "gpt-4",
            "litellm_call_id": "call-123",
            "litellm_params": {
                "metadata": {"user_api_key_alias": "alice"},
            },
        }
        cb.log_success_event(kwargs, MagicMock(usage=None), start, end)

        mock_tracer.start_as_current_span.assert_called_once_with("llm.request")
        attrs = {call.args[0]: call.args[1] for call in mock_span.set_attribute.call_args_list}
        assert attrs["llm.model"] == "gpt-4"
        assert attrs["llm.success"] is True
        assert attrs["llm.user"] == "alice"
        assert attrs["llm.duration_ms"] == 1500

    def test_records_usage_tokens(self, monkeypatch):
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s: mock_span
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(tracing_module, "_tracer", mock_tracer)
        monkeypatch.setattr(tracing_module, "_OTEL_AVAILABLE", True)

        cb = AirlockTracingCallback()
        usage = MagicMock(total_tokens=42)
        response = MagicMock(usage=usage)
        kwargs = {
            "model": "gpt-4",
            "litellm_call_id": "",
            "litellm_params": {"metadata": {}},
        }
        cb.log_success_event(kwargs, response, datetime.now(), datetime.now())

        attrs = {call.args[0]: call.args[1] for call in mock_span.set_attribute.call_args_list}
        assert attrs["llm.tokens.total"] == 42


class TestAirlockTracingCallbackFailure:
    def test_creates_span_with_error(self, monkeypatch):
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = lambda s: mock_span
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(tracing_module, "_tracer", mock_tracer)
        monkeypatch.setattr(tracing_module, "_OTEL_AVAILABLE", True)

        cb = AirlockTracingCallback()
        error = ValueError("boom")
        kwargs = {
            "model": "claude-3",
            "litellm_call_id": "call-456",
            "exception": error,
        }
        cb.log_failure_event(kwargs, None, None, None)

        attrs = {call.args[0]: call.args[1] for call in mock_span.set_attribute.call_args_list}
        assert attrs["llm.model"] == "claude-3"
        assert attrs["llm.success"] is False
        assert "boom" in attrs["llm.error"]
        mock_span.record_exception.assert_called_once_with(error)


class TestTracingNoOp:
    def test_noop_when_tracer_is_none(self, monkeypatch):
        monkeypatch.setattr(tracing_module, "_tracer", None)
        cb = AirlockTracingCallback()
        # Should not raise
        cb.log_success_event({"model": "x"}, None, None, None)
        cb.log_failure_event({"model": "x"}, None, None, None)
