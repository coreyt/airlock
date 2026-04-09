"""
Airlock S3 Logger — LiteLLM custom callback for writing logs to S3.

Writes the same structured JSON records as the enterprise logger, but
batches them in memory and flushes to S3 as JSONL files keyed by date.

Env vars:
    AIRLOCK_S3_BUCKET  — S3 bucket name (required)
    AIRLOCK_S3_PREFIX  — key prefix (default: "airlock-logs")
    AIRLOCK_S3_BATCH   — flush after this many records (default: 100)
"""

from __future__ import annotations

import atexit
import datetime
import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger("airlock.callbacks.s3")

try:
    import boto3
    from botocore.exceptions import ClientError

    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False

# Late import to avoid circular dependency
from litellm.integrations.custom_logger import CustomLogger

from .enterprise_logger import _redact_record, _serialize


_MAX_FLUSH_RETRIES = 3


class AirlockS3Logger(CustomLogger):
    """LiteLLM callback that batches and flushes log records to S3."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._bucket = os.getenv("AIRLOCK_S3_BUCKET", "")
        self._prefix = os.getenv("AIRLOCK_S3_PREFIX", "airlock-logs")
        self._batch_size = int(os.getenv("AIRLOCK_S3_BATCH", "100"))
        self._buffer: list[dict[str, Any]] = []
        self._flush_attempts: int = 0
        self._lock = threading.Lock()
        self._client = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not _BOTO3_AVAILABLE:
            raise ImportError("boto3 is required for S3 logging: pip install airlock[s3]")
        self._client = boto3.client("s3")
        return self._client

    def _build_record(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
        *,
        success: bool,
    ) -> dict[str, Any]:
        metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}
        usage: dict[str, int] = {}
        if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
            u = response_obj.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0),
                "completion_tokens": getattr(u, "completion_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }

        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "success": success,
            "model": kwargs.get("model", "unknown"),
            "user": metadata.get("user_api_key_alias") or metadata.get("user_api_key_user_id"),
            "team": metadata.get("user_api_key_team_alias"),
            "request_id": kwargs.get("litellm_call_id"),
            "messages": kwargs.get("messages"),
            "response": _serialize(response_obj) if response_obj else None,
            "error": str(kwargs.get("exception")) if not success else None,
            "duration_ms": (
                int((end_time - start_time).total_seconds() * 1000)
                if start_time and end_time
                else None
            ),
            **usage,
        }
        return _redact_record(record)

    def _flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            records = self._buffer[:]
            attempts = self._flush_attempts
            self._buffer.clear()

        if not self._bucket:
            logger.warning("AIRLOCK_S3_BUCKET not set, discarding %d records", len(records))
            return

        body = "\n".join(json.dumps(r, default=_serialize) for r in records) + "\n"
        now = datetime.datetime.now(datetime.timezone.utc)
        key = (
            f"{self._prefix}/{now.year:04d}/{now.month:02d}/{now.day:02d}/"
            f"airlock-{now.isoformat()}.jsonl"
        )

        try:
            client = self._get_client()
            client.put_object(Bucket=self._bucket, Key=key, Body=body.encode("utf-8"))
            logger.info("s3_flush bucket=%s key=%s records=%d", self._bucket, key, len(records))
            with self._lock:
                self._flush_attempts = 0
        except Exception:
            attempts += 1
            if attempts >= _MAX_FLUSH_RETRIES:
                logger.critical(
                    "s3_flush_dropped bucket=%s key=%s records=%d after %d attempts",
                    self._bucket, key, len(records), attempts,
                )
                with self._lock:
                    self._flush_attempts = 0
            else:
                logger.error(
                    "s3_flush_failed bucket=%s key=%s records=%d attempt=%d, re-queuing",
                    self._bucket, key, len(records), attempts,
                )
                with self._lock:
                    self._buffer.extend(records)
                    self._flush_attempts = attempts

    def _append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._buffer.append(record)
            should_flush = len(self._buffer) >= self._batch_size
        if should_flush:
            self._flush()

    # ------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------
    def log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        record = self._build_record(kwargs, response_obj, start_time, end_time, success=True)
        self._append(record)

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        import asyncio
        await asyncio.to_thread(self.log_success_event, kwargs, response_obj, start_time, end_time)

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------
    def log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        record = self._build_record(kwargs, response_obj, start_time, end_time, success=False)
        self._append(record)

    async def async_log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        import asyncio
        await asyncio.to_thread(self.log_failure_event, kwargs, response_obj, start_time, end_time)

    def flush(self) -> None:
        """Flush any buffered records to S3. Call on shutdown."""
        self._flush()


# Module-level instance for LiteLLM config.yaml callback registration.
proxy_s3_logger = AirlockS3Logger()
atexit.register(proxy_s3_logger.flush)
