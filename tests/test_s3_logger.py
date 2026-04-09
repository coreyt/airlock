"""Tests for airlock/callbacks/s3_logger.py"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from airlock.callbacks.s3_logger import AirlockS3Logger


class TestS3Logger:
    @pytest.fixture
    def s3_logger(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_S3_BUCKET", "test-bucket")
        monkeypatch.setenv("AIRLOCK_S3_PREFIX", "logs")
        monkeypatch.setenv("AIRLOCK_S3_BATCH", "3")
        logger = AirlockS3Logger()
        logger._client = MagicMock()
        return logger

    def test_build_record_success(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        record = s3_logger._build_record(
            mock_logger_kwargs, mock_response_obj, start, end, success=True
        )
        assert record["success"] is True
        assert record["model"] == "claude-sonnet"
        assert record["prompt_tokens"] == 25

    def test_build_record_applies_log_field_redaction(
        self,
        s3_logger,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
        monkeypatch,
    ):
        """S3 records must honor AIRLOCK_LOG_REDACT_FIELDS just like the file logger."""
        monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages,model")
        start, end = mock_start_end_times
        record = s3_logger._build_record(
            mock_logger_kwargs, mock_response_obj, start, end, success=True
        )
        assert record["messages"] == "[REDACTED]"
        assert record["model"] == "[REDACTED]"
        # Unlisted fields are untouched
        assert record["success"] is True

    def test_build_record_failure(
        self, s3_logger, mock_failure_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        record = s3_logger._build_record(
            mock_failure_kwargs, None, start, end, success=False
        )
        assert record["success"] is False
        assert "timeout" in record["error"]

    def test_log_success_buffers(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        s3_logger.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )
        assert len(s3_logger._buffer) == 1
        s3_logger._client.put_object.assert_not_called()

    def test_batch_triggers_flush(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        for _ in range(3):  # batch_size = 3
            s3_logger.log_success_event(
                mock_logger_kwargs, mock_response_obj, start, end
            )
        s3_logger._client.put_object.assert_called_once()
        assert len(s3_logger._buffer) == 0

    def test_flush_sends_correct_data(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        s3_logger.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )
        s3_logger._flush()

        call_kwargs = s3_logger._client.put_object.call_args
        assert call_kwargs.kwargs["Bucket"] == "test-bucket"
        assert call_kwargs.kwargs["Key"].startswith("logs/")
        assert call_kwargs.kwargs["Key"].endswith(".jsonl")

        body = call_kwargs.kwargs["Body"].decode("utf-8")
        record = json.loads(body.strip())
        assert record["model"] == "claude-sonnet"

    def test_flush_key_format(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        s3_logger.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )
        s3_logger._flush()

        key = s3_logger._client.put_object.call_args.kwargs["Key"]
        # Format: prefix/YYYY/MM/DD/airlock-<ts>.jsonl
        parts = key.split("/")
        assert parts[0] == "logs"
        assert len(parts[1]) == 4  # year
        assert len(parts[2]) == 2  # month
        assert len(parts[3]) == 2  # day
        assert parts[4].startswith("airlock-")

    def test_empty_flush_does_nothing(self, s3_logger):
        s3_logger._flush()
        s3_logger._client.put_object.assert_not_called()

    def test_failure_event_buffers(
        self, s3_logger, mock_failure_kwargs, mock_start_end_times
    ):
        start, end = mock_start_end_times
        s3_logger.log_failure_event(mock_failure_kwargs, None, start, end)
        assert len(s3_logger._buffer) == 1
        assert s3_logger._buffer[0]["success"] is False

    def test_no_bucket_discards(self, monkeypatch, mock_logger_kwargs, mock_response_obj, mock_start_end_times):
        monkeypatch.setenv("AIRLOCK_S3_BATCH", "1")
        logger = AirlockS3Logger()
        logger._client = MagicMock()
        monkeypatch.setattr(logger, "_bucket", "")

        start, end = mock_start_end_times
        logger.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)
        logger._client.put_object.assert_not_called()

    async def test_async_delegates(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        await s3_logger.async_log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )
        assert len(s3_logger._buffer) == 1

    def test_graceful_on_s3_error(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        start, end = mock_start_end_times
        s3_logger._client.put_object.side_effect = Exception("S3 unreachable")
        s3_logger.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )
        # Should not raise
        s3_logger._flush()

    def test_flush_failure_requeues_records(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        """On S3 failure, records should be re-buffered for retry."""
        start, end = mock_start_end_times
        s3_logger.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)
        s3_logger.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)
        assert len(s3_logger._buffer) == 2

        s3_logger._client.put_object.side_effect = Exception("S3 unreachable")
        s3_logger._flush()
        # Records should be back in buffer for retry
        assert len(s3_logger._buffer) == 2

    def test_flush_failure_retry_succeeds(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        """After a failed flush, a successful retry should clear the buffer."""
        start, end = mock_start_end_times
        s3_logger.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        # First flush fails
        s3_logger._client.put_object.side_effect = Exception("S3 unreachable")
        s3_logger._flush()
        assert len(s3_logger._buffer) == 1

        # Second flush succeeds
        s3_logger._client.put_object.side_effect = None
        s3_logger._flush()
        assert len(s3_logger._buffer) == 0
        assert s3_logger._client.put_object.call_count == 2

    def test_flush_drops_after_max_retries(
        self, s3_logger, mock_logger_kwargs, mock_response_obj, mock_start_end_times
    ):
        """Records should be dropped after max retry attempts with CRITICAL log."""
        start, end = mock_start_end_times
        s3_logger.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        s3_logger._client.put_object.side_effect = Exception("S3 unreachable")
        # Flush 3 times (max retries) — records should be re-queued each time
        s3_logger._flush()
        assert len(s3_logger._buffer) == 1
        s3_logger._flush()
        assert len(s3_logger._buffer) == 1
        # Third flush should drop the records
        s3_logger._flush()
        assert len(s3_logger._buffer) == 0


class TestS3GracefulDegradation:
    def test_module_loads_without_boto3(self):
        """Module imports fine even without boto3."""
        import airlock.callbacks.s3_logger as mod
        assert hasattr(mod, "AirlockS3Logger")
