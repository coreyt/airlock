"""
S13 — Logging: JSONL records, metadata, serialization.
"""

from __future__ import annotations

import datetime
import json

import pytest


pytestmark = pytest.mark.harness


# The historical ``AirlockLogger._build_record`` builder was deleted in the 0.5.4
# cutover; telemetry now flows event -> projection -> sink. This shim re-points the
# legacy record-shape assertions through the live path (field equivalence is also
# locked by tests/test_projections_equiv.py against the frozen goldens).
def _build_record(kwargs, response_obj, start_time, end_time, *, success):
    from airlock.callbacks.projections import project_enterprise
    from airlock.callbacks.request_event import build_request_event

    event = build_request_event(
        kwargs, response_obj, start_time, end_time, success=success
    )
    return project_enterprise(event)


class TestSuccessRecord:
    def test_all_fields_present(
        self,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        record = _build_record(
            mock_logger_kwargs, mock_response_obj, start, end, success=True
        )
        required = [
            "timestamp",
            "success",
            "model",
            "user",
            "request_id",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "duration_ms",
            "start_time",
            "end_time",
        ]
        for field in required:
            assert field in record, f"Missing field: {field}"

    def test_success_flag(
        self,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        record = _build_record(
            mock_logger_kwargs, mock_response_obj, start, end, success=True
        )
        assert record["success"] is True


class TestFailureRecord:
    def test_has_error(
        self,
        mock_failure_kwargs,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        record = _build_record(mock_failure_kwargs, None, start, end, success=False)
        assert record["success"] is False
        assert record.get("error") is not None


class TestLogFileNaming:
    def test_log_file_naming(self, harness_log_dir):
        from airlock.callbacks.enterprise_logger import _write_log

        _write_log({"test": "record"})
        today = datetime.date.today().isoformat()
        expected = harness_log_dir / f"airlock-{today}.jsonl"
        assert expected.exists()


class TestMCPLogging:
    def test_mcp_call_type_logged(
        self,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "unknown",
            "call_type": "call_mcp_tool",
            "messages": [{"role": "user", "content": "test"}],
            "litellm_call_id": "mcp-call-1",
            "litellm_params": {
                "metadata": {"mcp_server_name": "filesystem"},
            },
        }
        record = _build_record(kwargs, None, start, end, success=True)
        assert record.get("call_type") == "call_mcp_tool"

    def test_mcp_tool_name_logged(
        self,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "unknown",
            "call_type": "call_mcp_tool",
            "mcp_tool_name": "read_file",
            "messages": [],
            "litellm_call_id": "mcp-call-2",
            "litellm_params": {"metadata": {}},
        }
        record = _build_record(kwargs, None, start, end, success=True)
        assert record.get("mcp_tool_name") == "read_file"

    def test_mcp_server_name_logged(
        self,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "unknown",
            "call_type": "call_mcp_tool",
            "messages": [],
            "litellm_call_id": "mcp-call-3",
            "litellm_params": {
                "metadata": {"mcp_server_name": "filesystem"},
            },
        }
        record = _build_record(kwargs, None, start, end, success=True)
        assert record.get("mcp_server_name") == "filesystem"


class TestGuardrailMetadata:
    def test_pii_metadata_in_log(
        self,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "claude-sonnet",
            "messages": [],
            "litellm_call_id": "test-pii",
            "litellm_params": {
                "metadata": {
                    "airlock_pii_redacted": {"count": 2, "types": ["EMAIL", "SSN"]},
                },
            },
        }
        record = _build_record(kwargs, None, start, end, success=True)
        assert "airlock_pii_redacted" in record

    def test_enforcement_metadata(
        self,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "claude-sonnet",
            "messages": [],
            "litellm_call_id": "test-enforce",
            "litellm_params": {
                "metadata": {
                    "airlock_enforcement": {"mode": "shadow", "should_block": False},
                },
            },
        }
        record = _build_record(kwargs, None, start, end, success=True)
        assert "airlock_enforcement" in record

    def test_observation_metadata(
        self,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "claude-sonnet",
            "messages": [],
            "litellm_call_id": "test-obs",
            "litellm_params": {
                "metadata": {
                    "airlock_observation": {"composite_score": 0.3},
                },
            },
        }
        record = _build_record(kwargs, None, start, end, success=True)
        assert "airlock_observation" in record


class TestSerialization:
    def test_all_lines_valid_json(self, harness_log_dir):
        from airlock.callbacks.enterprise_logger import _write_log

        _write_log({"key1": "value1"})
        _write_log({"key2": "value2"})
        today = datetime.date.today().isoformat()
        log_file = harness_log_dir / f"airlock-{today}.jsonl"
        for line in log_file.read_text().strip().split("\n"):
            json.loads(line)  # Should not raise

    def test_datetime_serialized(self, harness_log_dir):
        from airlock.callbacks.enterprise_logger import _write_log

        record = {"ts": datetime.datetime(2024, 1, 1, 12, 0, 0)}
        _write_log(record)
        today = datetime.date.today().isoformat()
        log_file = harness_log_dir / f"airlock-{today}.jsonl"
        last_line = log_file.read_text().strip().split("\n")[-1]
        parsed = json.loads(last_line)
        assert "2024-01-01" in str(parsed["ts"])

    def test_bytes_serialized(self, harness_log_dir):
        from airlock.callbacks.enterprise_logger import _write_log

        record = {"data": b"hello bytes"}
        _write_log(record)
        today = datetime.date.today().isoformat()
        log_file = harness_log_dir / f"airlock-{today}.jsonl"
        last_line = log_file.read_text().strip().split("\n")[-1]
        parsed = json.loads(last_line)
        assert "hello bytes" in str(parsed["data"])

    def test_pydantic_serialized(self, harness_log_dir):
        from airlock.callbacks.enterprise_logger import _write_log

        class FakeModel:
            def model_dump(self):
                return {"key": "value"}

        record = {"nested": FakeModel()}
        _write_log(record)
        today = datetime.date.today().isoformat()
        log_file = harness_log_dir / f"airlock-{today}.jsonl"
        last_line = log_file.read_text().strip().split("\n")[-1]
        parsed = json.loads(last_line)
        assert parsed["nested"]["key"] == "value"
