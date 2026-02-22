"""
Airlock Enterprise Logger — LiteLLM custom callback.

Captures every LLM request and response and writes structured JSON logs
that can be shipped to Splunk, Datadog, S3, or any SIEM.

Usage in config.yaml:
    litellm_settings:
        success_callback: ["airlock.callbacks.enterprise_logger"]
        failure_callback: ["airlock.callbacks.enterprise_logger"]

Env vars:
    AIRLOCK_LOG_DIR  — directory for JSON log files (default: ./logs)
"""

from __future__ import annotations

import json
import logging
import os
import datetime
from pathlib import Path
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("airlock.logger")

LOG_DIR = Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _serialize(obj: Any) -> Any:
    """Make objects JSON-serializable."""
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if hasattr(obj, "model_dump"):  # pydantic v2
        return obj.model_dump()
    if hasattr(obj, "dict"):  # pydantic v1
        return obj.dict()
    return str(obj)


def _write_log(record: dict[str, Any]) -> None:
    """Append a JSON record to today's log file."""
    _ensure_log_dir()
    today = datetime.date.today().isoformat()
    log_path = LOG_DIR / f"airlock-{today}.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=_serialize) + "\n")


class AirlockLogger(CustomLogger):
    """LiteLLM callback that logs requests/responses to structured JSON files.

    LiteLLM's ``get_instance_fn`` returns whatever ``getattr`` finds at the
    dotted path — it does **not** instantiate classes.  The module-level
    ``proxy_logger`` instance below is the object that config.yaml should
    reference so that ``isinstance(callback, CustomLogger)`` passes inside
    LiteLLM's logging pipeline.
    """

    # ------------------------------------------------------------------
    # Success
    # ------------------------------------------------------------------
    def log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        record = self._build_record(kwargs, response_obj, start_time, end_time, success=True)
        _write_log(record)
        logger.info("request_logged model=%s user=%s tokens=%s", record["model"], record.get("user"), record.get("total_tokens"))

    async def async_log_success_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------
    def log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        record = self._build_record(kwargs, response_obj, start_time, end_time, success=False)
        _write_log(record)
        logger.warning("request_failed model=%s user=%s error=%s", record["model"], record.get("user"), record.get("error"))

    async def async_log_failure_event(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any) -> None:
        self.log_failure_event(kwargs, response_obj, start_time, end_time)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_record(
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
        *,
        success: bool,
    ) -> dict[str, Any]:
        metadata = kwargs.get("litellm_params", {}).get("metadata", {}) or {}

        # Token usage
        usage: dict[str, int] = {}
        if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
            u = response_obj.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0),
                "completion_tokens": getattr(u, "completion_tokens", 0),
                "total_tokens": getattr(u, "total_tokens", 0),
            }

        # Collect airlock_* guardrail metadata (semantic scores, priority,
        # failover info) so the slow analyzer can see classifier verdicts.
        guardrail_meta = {
            k: v for k, v in metadata.items() if k.startswith("airlock_")
        }

        return {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "success": success,
            "model": kwargs.get("model", "unknown"),
            "user": metadata.get("user_api_key_alias") or metadata.get("user_api_key_user_id"),
            "team": metadata.get("user_api_key_team_alias"),
            "request_id": kwargs.get("litellm_call_id"),
            "messages": kwargs.get("messages"),
            "response": _serialize(response_obj) if response_obj else None,
            "error": str(kwargs.get("exception")) if not success else None,
            "start_time": start_time,
            "end_time": end_time,
            "duration_ms": (
                int((end_time - start_time).total_seconds() * 1000)
                if start_time and end_time
                else None
            ),
            **usage,
            **guardrail_meta,
        }


# Module-level instance for config.yaml callback registration.
# LiteLLM's get_instance_fn does getattr — it needs an instance, not a class.
# We also self-register into the async callback lists because the proxy runs
# async but config's success_callback key only populates the sync list.
proxy_logger = AirlockLogger()


def _self_register() -> None:
    """Ensure proxy_logger is in both sync and async callback lists."""
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_success_callback(proxy_logger)
        mgr.add_litellm_failure_callback(proxy_logger)
        mgr.add_litellm_async_success_callback(proxy_logger)
        mgr.add_litellm_async_failure_callback(proxy_logger)
    except Exception:
        pass  # litellm not fully loaded yet — config path will handle it


_self_register()
