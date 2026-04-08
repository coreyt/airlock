"""Tests for airlock/callbacks/enterprise_logger.py"""

from __future__ import annotations

import datetime
import json
import logging
from unittest.mock import MagicMock

import pytest

from airlock.callbacks.enterprise_logger import (
    AirlockLogger,
    _cleanup_old_logs,
    _redact_record,
    _rotate_if_oversized,
    _serialize,
    _write_log,
    write_precall_block_record,
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
        assert record["error_type"] == "Exception"
        assert record["failure_category"] == "provider"
        assert record["response"] is None

    def test_failure_record_uses_airlock_client_env(
        self, mock_failure_kwargs, mock_start_end_times, monkeypatch
    ):
        monkeypatch.setenv("AIRLOCK_CLIENT", "dashboard-test-client")
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            mock_failure_kwargs, None, start, end, success=False
        )
        assert record["airlock_client"] == "dashboard-test-client"

    def test_record_prefers_incoming_airlock_client_header(
        self, mock_failure_kwargs, mock_start_end_times, monkeypatch
    ):
        monkeypatch.setenv("AIRLOCK_CLIENT", "proxy-process-client")
        kwargs = {
            **mock_failure_kwargs,
            "headers": {"X-Airlock-Client": "incoming-client"},
        }
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            kwargs, None, start, end, success=False
        )
        assert record["airlock_client"] == "incoming-client"

    def test_blank_failure_marked_eval(
        self, mock_logger_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        kwargs = {
            **mock_logger_kwargs,
            "messages": [
                {"role": "system", "content": "Judge this response."},
                {
                    "role": "user",
                    "content": (
                        "User input: Save this article about Rust\n\n"
                        "Evaluation question: Did the assistant confirm it saved it?"
                    ),
                },
            ],
            "exception": Exception(),
        }
        record = AirlockLogger._build_record(
            kwargs, None, start, end, success=False
        )
        assert record["error"] == "Evaluation request failed before provider call"
        assert record["error_type"] == "Exception"
        assert record["failure_category"] == "eval"

    def test_missing_exception_marked_eval(
        self, mock_logger_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        kwargs = {
            **mock_logger_kwargs,
            "messages": [{"role": "user", "content": "Evaluation question: Did it work?"}],
        }
        record = AirlockLogger._build_record(
            kwargs, None, start, end, success=False
        )
        assert record["error"] == "Evaluation request failed before provider call"
        assert record["error_type"] is None
        assert record["failure_category"] == "eval"

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


class TestPrecallBlockRecord:
    def test_writes_provider_protection_record(self, monkeypatch):
        written: list[dict] = []

        monkeypatch.setattr(
            "airlock.callbacks.enterprise_logger._write_log",
            lambda record: written.append(record),
        )

        record = write_precall_block_record(
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Hello"}],
                "headers": {"X-Airlock-Client": "blocked-client"},
                "metadata": {
                    "airlock_provider": "openai",
                    "airlock_request": {
                        "client_id": "blocked-client",
                        "requested_model": "gpt-4o-mini",
                        "final_model": "gpt-4o-mini",
                        "pinned_model": True,
                        "provider": "openai",
                    },
                    "airlock_provider_protection": {
                        "action": "blocked_429",
                        "scope": "client_provider",
                        "client_id": "blocked-client",
                        "provider": "openai",
                        "requested_model": "gpt-4o-mini",
                        "final_model": "gpt-4o-mini",
                        "reason": "quota",
                        "cooldown_seconds": 300.0,
                    },
                },
            },
            error="Airlock temporarily blocked client blocked-client from provider openai",
            error_type="RateLimitError",
        )

        assert written
        assert record["success"] is False
        assert record["failure_category"] == "provider"
        assert record["airlock_client"] == "blocked-client"
        assert record["airlock_provider"] == "openai"
        assert record["airlock_provider_protection"]["action"] == "blocked_429"

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
        assert set(airlock_keys) == {"airlock_client", "airlock_provider"}

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

    def test_missing_client_normalized_in_record(self, mock_start_end_times):
        kwargs = {
            "model": "gpt-4o",
            "messages": [],
            "litellm_params": {"metadata": {}},
        }
        start, end = mock_start_end_times
        record = AirlockLogger._build_record(
            kwargs, None, start, end, success=True
        )
        assert record["airlock_client"] == "no_client"
        assert record["airlock_provider"] == "openai"

    def test_gemini_success_adds_gemini_metadata_and_headers(
        self, mock_logger_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        kwargs = {
            **mock_logger_kwargs,
            "model": "gemini-pro",
            "litellm_params": {
                "metadata": {
                    **mock_logger_kwargs["litellm_params"]["metadata"],
                    "airlock_gemini": {"mode": "deep_reasoning"},
                }
            },
        }
        response = MagicMock()
        response.usage.prompt_tokens = 10
        response.usage.completion_tokens = 5
        response.usage.total_tokens = 15
        response.model_dump.return_value = {
            "choices": [{"message": {"content": None}, "finish_reason": "length"}],
            "usage": {"completion_tokens_details": {"reasoning_tokens": 5, "text_tokens": 0}},
        }
        record = AirlockLogger._build_record(
            kwargs, response, start, end, success=True
        )
        assert record["airlock_gemini"]["mode"] == "deep_reasoning"
        assert record["airlock_gemini_response"]["output_shape"] == "thought_only"
        assert record["airlock_response_headers"]["X-Airlock-Provider-Mode"] == "gemini"


# ---------------------------------------------------------------------------
# _write_log() and file I/O
# ---------------------------------------------------------------------------
class TestWriteLog:
    def test_creates_log_dir_if_missing(self, tmp_path, monkeypatch):
        import airlock.callbacks.enterprise_logger as mod

        log_path = tmp_path / "new_logs"
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_path))
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

    def test_log_failure_event_warns_with_client_and_category(
        self, log_dir, mock_failure_kwargs, mock_start_end_times, monkeypatch, caplog
    ):
        monkeypatch.setenv("AIRLOCK_CLIENT", "dashboard-test-client")
        start, end = mock_start_end_times
        logger = AirlockLogger()

        with caplog.at_level(logging.WARNING, logger="airlock.logger"):
            logger.log_failure_event(mock_failure_kwargs, None, start, end)

        assert "client=dashboard-test-client" in caplog.text
        assert "category=provider" in caplog.text

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


# ---------------------------------------------------------------------------
# Log rotation and cleanup
# ---------------------------------------------------------------------------
class TestLogCleanup:
    def test_cleanup_removes_old_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._log_dir", lambda: tmp_path)
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._max_log_days", lambda: 30)
        old_date = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        old_file = tmp_path / f"airlock-{old_date}.jsonl"
        old_file.write_text("{}\n")
        _cleanup_old_logs()
        assert not old_file.exists()

    def test_cleanup_keeps_recent_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._log_dir", lambda: tmp_path)
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._max_log_days", lambda: 30)
        recent_date = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        recent_file = tmp_path / f"airlock-{recent_date}.jsonl"
        recent_file.write_text("{}\n")
        _cleanup_old_logs()
        assert recent_file.exists()

    def test_cleanup_respects_max_log_days_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._log_dir", lambda: tmp_path)
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._max_log_days", lambda: 7)
        old_date = (datetime.date.today() - datetime.timedelta(days=10)).isoformat()
        old_file = tmp_path / f"airlock-{old_date}.jsonl"
        old_file.write_text("{}\n")
        _cleanup_old_logs()
        assert not old_file.exists()

    def test_cleanup_handles_permission_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._log_dir", lambda: tmp_path)
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._max_log_days", lambda: 30)
        old_date = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        old_file = tmp_path / f"airlock-{old_date}.jsonl"
        old_file.write_text("{}\n")
        from unittest.mock import patch as mock_patch
        with mock_patch.object(type(old_file), "unlink", side_effect=OSError("permission denied")):
            _cleanup_old_logs()  # should not crash


class TestLogRotation:
    def test_rotate_oversized_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._max_log_size_mb", lambda: 1)
        log_file = tmp_path / "airlock-2026-04-06.jsonl"
        log_file.write_text("x" * (2 * 1024 * 1024))  # 2MB > 1MB limit
        _rotate_if_oversized(log_file)
        assert not log_file.exists()
        assert (tmp_path / "airlock-2026-04-06.1.jsonl").exists()

    def test_no_rotate_under_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._max_log_size_mb", lambda: 500)
        log_file = tmp_path / "airlock-2026-04-06.jsonl"
        log_file.write_text("small log\n")
        _rotate_if_oversized(log_file)
        assert log_file.exists()  # not rotated

    def test_rotate_increments_suffix(self, tmp_path, monkeypatch):
        monkeypatch.setattr("airlock.callbacks.enterprise_logger._max_log_size_mb", lambda: 1)
        log_file = tmp_path / "airlock-2026-04-06.jsonl"
        (tmp_path / "airlock-2026-04-06.1.jsonl").write_text("old rotated\n")
        log_file.write_text("x" * (2 * 1024 * 1024))
        _rotate_if_oversized(log_file)
        assert not log_file.exists()
        assert (tmp_path / "airlock-2026-04-06.2.jsonl").exists()


# ---------------------------------------------------------------------------
# Disk-full handling (P1 Fix #1)
# ---------------------------------------------------------------------------
class TestDiskFullHandling:
    def test_write_log_survives_oserror(self, tmp_path, monkeypatch, caplog):
        """_write_log swallows OSError (disk full) and logs to stderr."""
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path / "logs"))
        # Make _ensure_log_dir succeed but open() fail
        monkeypatch.setattr(
            "airlock.callbacks.enterprise_logger._ensure_log_dir",
            lambda: tmp_path / "logs",
        )
        from unittest.mock import mock_open, patch as mock_patch
        m = mock_open()
        m.side_effect = OSError("No space left on device")
        with mock_patch("builtins.open", m):
            with caplog.at_level(logging.ERROR, logger="airlock.logger"):
                # Should NOT raise
                _write_log({"test": "disk_full"})
        assert "No space left on device" in caplog.text

    def test_log_success_event_survives_disk_full(
        self, tmp_path, monkeypatch, mock_logger_kwargs, mock_response_obj,
        mock_start_end_times, caplog,
    ):
        """AirlockLogger.log_success_event does not raise on disk full."""
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(
            "airlock.callbacks.enterprise_logger._ensure_log_dir",
            lambda: tmp_path / "logs",
        )
        from unittest.mock import mock_open, patch as mock_patch
        m = mock_open()
        m.side_effect = OSError("No space left on device")
        start, end = mock_start_end_times
        logger_inst = AirlockLogger()
        with mock_patch("builtins.open", m):
            with caplog.at_level(logging.ERROR, logger="airlock.logger"):
                # Should NOT raise
                logger_inst.log_success_event(
                    mock_logger_kwargs, mock_response_obj, start, end
                )

    def test_log_failure_event_survives_disk_full(
        self, tmp_path, monkeypatch, mock_failure_kwargs,
        mock_start_end_times, caplog,
    ):
        """AirlockLogger.log_failure_event does not raise on disk full."""
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setattr(
            "airlock.callbacks.enterprise_logger._ensure_log_dir",
            lambda: tmp_path / "logs",
        )
        from unittest.mock import mock_open, patch as mock_patch
        m = mock_open()
        m.side_effect = OSError("No space left on device")
        start, end = mock_start_end_times
        logger_inst = AirlockLogger()
        with mock_patch("builtins.open", m):
            with caplog.at_level(logging.ERROR, logger="airlock.logger"):
                logger_inst.log_failure_event(
                    mock_failure_kwargs, None, start, end
                )


# ---------------------------------------------------------------------------
# Log field redaction
# ---------------------------------------------------------------------------
class TestLogFieldRedaction:
    def test_no_env_var_logs_normally(self):
        """Default behavior: no redaction, backward compatible."""
        record = {
            "messages": [{"role": "user", "content": "Hello"}],
            "response": {"choices": [{"message": {"content": "Hi"}}]},
            "model": "claude-sonnet",
        }
        result = _redact_record(record)
        assert result["messages"] == [{"role": "user", "content": "Hello"}]
        assert result["response"] == {"choices": [{"message": {"content": "Hi"}}]}

    def test_empty_env_var_no_redaction(self, monkeypatch):
        """Empty AIRLOCK_LOG_REDACT_FIELDS means no redaction."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "")
        record = {
            "messages": [{"role": "user", "content": "secret"}],
            "response": "some response",
        }
        result = _redact_record(record)
        assert result["messages"] == [{"role": "user", "content": "secret"}]
        assert result["response"] == "some response"

    def test_redact_single_field(self, monkeypatch):
        """Redacting a single field replaces its value with [REDACTED]."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages")
        record = {
            "messages": [{"role": "user", "content": "Tell me a secret"}],
            "response": "No secrets here",
            "model": "claude-sonnet",
        }
        result = _redact_record(record)
        assert result["messages"] == "[REDACTED]"
        assert result["response"] == "No secrets here"
        assert result["model"] == "claude-sonnet"

    def test_redact_multiple_fields(self, monkeypatch):
        """Multiple comma-separated fields are all redacted."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages,response")
        record = {
            "messages": [{"role": "user", "content": "PII data"}],
            "response": {"choices": [{"message": {"content": "secret output"}}]},
            "model": "gpt-4o",
            "user": "alice",
        }
        result = _redact_record(record)
        assert result["messages"] == "[REDACTED]"
        assert result["response"] == "[REDACTED]"
        assert result["model"] == "gpt-4o"
        assert result["user"] == "alice"

    def test_redact_fields_with_whitespace(self, monkeypatch):
        """Whitespace around field names is stripped."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", " messages , response ")
        record = {
            "messages": [{"role": "user", "content": "data"}],
            "response": "output",
        }
        result = _redact_record(record)
        assert result["messages"] == "[REDACTED]"
        assert result["response"] == "[REDACTED]"

    def test_nonexistent_field_silently_ignored(self, monkeypatch):
        """Fields not present in the record are silently skipped."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "nonexistent_field,messages")
        record = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = _redact_record(record)
        assert result["messages"] == "[REDACTED]"
        assert result["model"] == "claude-sonnet"
        assert "nonexistent_field" not in result

    def test_redacted_list_field(self, monkeypatch):
        """A list field (messages) is replaced with [REDACTED], not per-element."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages")
        record = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "My SSN is 123-45-6789"},
            ],
        }
        result = _redact_record(record)
        assert result["messages"] == "[REDACTED]"

    def test_redact_does_not_mutate_original(self, monkeypatch):
        """Redaction returns a new dict; original record is untouched."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages")
        record = {
            "messages": [{"role": "user", "content": "original"}],
            "model": "gpt-4o",
        }
        result = _redact_record(record)
        assert result["messages"] == "[REDACTED]"
        assert record["messages"] == [{"role": "user", "content": "original"}]

    def test_redaction_applied_in_write_log(self, log_dir, monkeypatch):
        """End-to-end: _write_log applies redaction before writing."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages,response")
        record = {
            "messages": [{"role": "user", "content": "sensitive prompt"}],
            "response": {"choices": [{"message": {"content": "sensitive output"}}]},
            "model": "claude-sonnet",
            "timestamp": "2024-01-15T10:00:00",
        }
        _write_log(record)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        written = json.loads(log_path.read_text().strip())
        assert written["messages"] == "[REDACTED]"
        assert written["response"] == "[REDACTED]"
        assert written["model"] == "claude-sonnet"

    def test_redaction_applied_in_precall_block_record(self, log_dir, monkeypatch):
        """write_precall_block_record applies redaction at write time."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages")
        write_precall_block_record(
            {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "sensitive"}],
                "metadata": {},
            },
            error="blocked",
            error_type="RateLimitError",
        )
        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        written = json.loads(log_path.read_text().strip())
        assert written["messages"] == "[REDACTED]"
        assert written["model"] == "gpt-4o"
