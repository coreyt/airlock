"""Tests for airlock/callbacks/enterprise_logger.py"""

from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock

import pytest

from airlock.callbacks.enterprise_logger import (
    AirlockLogger,
    _serialize,
    _write_log,
)


# ---------------------------------------------------------------------------
# _serialize()
# ---------------------------------------------------------------------------
class TestSerialize:
    def test_datetime_to_isoformat(self):
        dt = datetime.datetime(2024, 1, 15, 10, 30, 0)
        assert _serialize(dt) == "2024-01-15T10:30:00"

    def test_bytes_to_string(self):
        assert _serialize(b"hello") == "hello"

    def test_bytes_with_invalid_utf8(self):
        result = _serialize(b"\xff\xfe")
        assert isinstance(result, str)

    def test_pydantic_v2_model_dump(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"key": "value"}
        del obj.dict  # ensure model_dump is tried first
        assert _serialize(obj) == {"key": "value"}

    def test_pydantic_v1_dict(self):
        obj = MagicMock(spec=[])
        obj.dict = MagicMock(return_value={"key": "value"})
        # No model_dump attribute
        assert not hasattr(obj, "model_dump")
        assert _serialize(obj) == {"key": "value"}

    def test_unknown_type_to_str(self):
        assert _serialize(42) == "42"
        assert _serialize(None) == "None"
        assert _serialize([1, 2]) == "[1, 2]"


# ---------------------------------------------------------------------------
# AirlockLogger._build_record()
# ---------------------------------------------------------------------------
class TestBuildRecord:
    def test_success_record_has_all_fields(
        self, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            mock_logger_kwargs, mock_response_obj, start, end, success=True
        )

        assert "timestamp" in record
        assert record["success"] is True
        assert record["model"] == "claude-sonnet"
        assert record["user"] == "dev-alice"
        assert record["team"] == "engineering"
        assert record["request_id"] == "call-abc-123"
        assert record["messages"] == [{"role": "user", "content": "Hello"}]
        assert record["response"] is not None
        assert record["error"] is None
        assert record["start_time"] == start
        assert record["end_time"] == end
        assert record["prompt_tokens"] == 25
        assert record["completion_tokens"] == 50
        assert record["total_tokens"] == 75

    def test_duration_ms_correct(
        self, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            mock_logger_kwargs, mock_response_obj, start, end, success=True
        )
        assert record["duration_ms"] == 1500

    def test_failure_record_has_error(
        self, mock_failure_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            mock_failure_kwargs, None, start, end, success=False
        )
        assert record["success"] is False
        assert record["error"] == "Model timeout after 300s"
        assert record["response"] is None

    def test_missing_response_obj(self, mock_logger_kwargs, mock_start_end_times):
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            mock_logger_kwargs, None, start, end, success=True
        )
        assert record["response"] is None
        assert record.get("prompt_tokens") is None or record.get("prompt_tokens") == 0

    def test_missing_usage(self, mock_logger_kwargs, mock_start_end_times):
        start, end = mock_start_end_times
        response = MagicMock()
        response.usage = None
        record = AirlockLogger._build_record(
            mock_logger_kwargs, response, start, end, success=True
        )
        assert "prompt_tokens" not in record or record.get("prompt_tokens", 0) == 0

    def test_missing_start_end_times(self, mock_logger_kwargs, mock_response_obj):
        record = AirlockLogger._build_record(
            mock_logger_kwargs, mock_response_obj, None, None, success=True
        )
        assert record["duration_ms"] is None

    def test_user_falls_back_to_user_id(self):
        kwargs = {
            "model": "gpt-4o",
            "messages": [],
            "litellm_params": {
                "metadata": {
                    "user_api_key_user_id": "bob",
                    "user_api_key_team_alias": "research",
                }
            },
        }
        record = AirlockLogger._build_record(
            kwargs, None, None, None, success=True
        )
        assert record["user"] == "bob"

    def test_airlock_metadata_included(self):
        """Guardrail metadata (airlock_*) is passed through to log records."""
        kwargs = {
            "model": "claude-sonnet",
            "messages": [],
            "litellm_params": {
                "metadata": {
                    "user_api_key_alias": "alice",
                    "airlock_semantic": {
                        "status": "passed",
                        "blocking_classifier": None,
                        "total_duration_ms": 42.5,
                        "results": [
                            {"name": "injection", "score": 0.12, "blocked": False}
                        ],
                    },
                    "airlock_priority": {"score": 0.5, "boost": False},
                }
            },
        }
        record = AirlockLogger._build_record(
            kwargs, None, None, None, success=True
        )
        assert "airlock_semantic" in record
        assert record["airlock_semantic"]["status"] == "passed"
        assert record["airlock_semantic"]["results"][0]["name"] == "injection"
        assert "airlock_priority" in record
        assert record["airlock_priority"]["score"] == 0.5

    def test_non_airlock_metadata_excluded(self):
        """Non-airlock metadata keys are not leaked to log records."""
        kwargs = {
            "model": "claude-sonnet",
            "messages": [],
            "litellm_params": {
                "metadata": {
                    "user_api_key_alias": "alice",
                    "some_internal_field": "secret",
                    "airlock_semantic": {"status": "passed"},
                }
            },
        }
        record = AirlockLogger._build_record(
            kwargs, None, None, None, success=True
        )
        assert "airlock_semantic" in record
        assert "some_internal_field" not in record

    def test_no_airlock_metadata_no_extra_keys(self, mock_logger_kwargs, mock_start_end_times):
        """When no airlock_* metadata exists, no extra keys are added."""
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            mock_logger_kwargs, None, start, end, success=True
        )
        airlock_keys = [k for k in record if k.startswith("airlock_")]
        assert airlock_keys == []

    def test_observation_in_record(self, mock_start_end_times):
        """airlock_observation from metadata flows into the log record."""
        observation = {
            "request_id": "req-001",
            "model": "claude-sonnet",
            "client_id": "key:testkey1",
            "signals": [{"guardrail_name": "pii_scan", "detected": True}],
        }
        kwargs = {
            "model": "claude-sonnet",
            "messages": [],
            "litellm_params": {
                "metadata": {"airlock_observation": observation}
            },
        }
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            kwargs, None, start, end, success=True
        )
        assert record["airlock_observation"] == observation

    def test_observation_absent_not_in_record(self, mock_logger_kwargs, mock_start_end_times):
        """Without observation metadata, no airlock_observation key appears."""
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            mock_logger_kwargs, None, start, end, success=True
        )
        assert "airlock_observation" not in record

    def test_enforcement_in_record(self, mock_start_end_times):
        """airlock_enforcement from metadata flows into the log record."""
        enforcement = {"mode": "shadow", "should_block": False}
        kwargs = {
            "model": "claude-sonnet",
            "messages": [],
            "litellm_params": {
                "metadata": {"airlock_enforcement": enforcement}
            },
        }
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            kwargs, None, start, end, success=True
        )
        assert record["airlock_enforcement"] == enforcement


# ---------------------------------------------------------------------------
# _write_log() and file I/O
# ---------------------------------------------------------------------------
class TestWriteLog:
    def test_creates_log_dir_if_missing(self, tmp_path, monkeypatch):
        import airlock.callbacks.enterprise_logger as mod

        log_path = tmp_path / "new_logs"
        monkeypatch.setattr(mod, "LOG_DIR", log_path)
        _write_log({"test": "record"})
        assert log_path.exists()

    def test_file_named_with_date(self, log_dir):
        _write_log({"test": "record"})
        today = datetime.date.today().isoformat()
        expected = log_dir / f"airlock-{today}.jsonl"
        assert expected.exists()

    def test_each_line_is_valid_json(self, log_dir):
        _write_log({"key1": "value1"})
        _write_log({"key2": "value2"})

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_appends_to_existing_file(self, log_dir):
        _write_log({"first": True})
        _write_log({"second": True})

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["first"] is True
        assert json.loads(lines[1])["second"] is True

    def test_serializes_datetime_in_record(self, log_dir):
        _write_log({"timestamp": datetime.datetime(2024, 1, 15)})
        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())
        assert record["timestamp"] == "2024-01-15T00:00:00"


# ---------------------------------------------------------------------------
# Callback methods
# ---------------------------------------------------------------------------
class TestCallbackMethods:
    def test_log_success_event(
        self, log_dir, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        logger = AirlockLogger()
        logger.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())
        assert record["success"] is True
        assert record["model"] == "claude-sonnet"

    def test_log_failure_event(
        self, log_dir, mock_failure_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        logger = AirlockLogger()
        logger.log_failure_event(mock_failure_kwargs, None, start, end)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())
        assert record["success"] is False
        assert "timeout" in record["error"]

    async def test_async_log_success_delegates(
        self, log_dir, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        logger = AirlockLogger()
        await logger.async_log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        assert log_path.exists()

    async def test_async_log_failure_delegates(
        self, log_dir, mock_failure_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        logger = AirlockLogger()
        await logger.async_log_failure_event(
            mock_failure_kwargs, None, start, end
        )

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        assert log_path.exists()


# ---------------------------------------------------------------------------
# MCP metadata in JSONL records
# ---------------------------------------------------------------------------
class TestMCPLogging:
    def test_mcp_fields_in_record(self, log_dir, mock_start_end_times):
        """MCP call_type, tool name, and server name appear in JSONL."""
        start, end = mock_start_end_times
        kwargs = {
            "model": "unknown",
            "call_type": "call_mcp_tool",
            "mcp_tool_name": "read_file",
            "messages": [{"role": "user", "content": "synthetic"}],
            "litellm_call_id": "call-mcp-123",
            "litellm_params": {
                "metadata": {
                    "mcp_server_name": "filesystem",
                },
            },
        }

        logger = AirlockLogger()
        logger.log_success_event(kwargs, None, start, end)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())

        assert record["call_type"] == "call_mcp_tool"
        assert record["mcp_tool_name"] == "read_file"
        assert record["mcp_server_name"] == "filesystem"

    def test_llm_call_no_mcp_fields(self, log_dir, mock_logger_kwargs, mock_response_obj, mock_start_end_times):
        """Regular LLM calls should NOT have MCP fields in the record."""
        start, end = mock_start_end_times
        logger = AirlockLogger()
        logger.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())

        assert "call_type" not in record
        assert "mcp_tool_name" not in record
