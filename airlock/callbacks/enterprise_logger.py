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
import re
import datetime
from pathlib import Path
from typing import Any

from airlock.client_identity import extract_airlock_client_from_kwargs
from airlock.gemini_interface import (
    build_gemini_response_headers,
    classify_gemini_response,
    is_gemini_provider,
)
from airlock.fast.router import infer_provider
from airlock.fast.state import normalize_client_id
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("airlock.logger")


def _log_dir() -> Path:
    return Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))


def _ensure_log_dir() -> Path:
    d = _log_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def _get_airlock_client(metadata: dict[str, Any], kwargs: dict[str, Any]) -> str | None:
    """Return the best available Airlock client identifier."""
    client = (
        metadata.get("airlock_client")
        or extract_airlock_client_from_kwargs(kwargs)
        or os.getenv("AIRLOCK_CLIENT")
        or metadata.get("client_id")
    )
    return normalize_client_id(client)


def _normalize_failure(
    kwargs: dict[str, Any],
    response_obj: Any,
) -> tuple[str, str | None, str]:
    """Return a stable failure message, type, and category.

    Categories:
      - provider: request reached the model/provider layer and failed there
      - eval: request failed before a provider response was produced
    """
    exc = kwargs.get("exception")
    exc_type = type(exc).__name__ if exc is not None else None

    error_text = ""
    if exc is not None:
        error_text = str(exc).strip()
        if not error_text:
            exc_repr = repr(exc).strip()
            empty_repr = f"{type(exc).__name__}()"
            if exc_repr != empty_repr:
                error_text = exc_repr

    messages = kwargs.get("messages") or []
    last_content = ""
    if messages:
        last = messages[-1]
        if isinstance(last, dict):
            content = last.get("content", "")
            last_content = str(content)

    provider_failure = bool(error_text)
    if response_obj is not None:
        provider_failure = True

    if provider_failure:
        category = "provider"
    elif "Evaluation question:" in last_content:
        category = "eval"
    else:
        category = "pre_call"

    if error_text:
        return error_text, exc_type, category

    synthetic = "No exception details captured before provider call"
    if "Evaluation question:" in last_content:
        synthetic = "Evaluation request failed before provider call"
    return synthetic, exc_type, category


def _max_log_days() -> int:
    return int(os.getenv("AIRLOCK_MAX_LOG_DAYS", "30"))


def _max_log_size_mb() -> int:
    return int(os.getenv("AIRLOCK_MAX_LOG_SIZE_MB", "500"))


_LOG_DATE_RE = re.compile(r"^airlock-(\d{4}-\d{2}-\d{2})(?:\.\d+)?\.jsonl$")


def _cleanup_old_logs() -> None:
    """Remove log files older than AIRLOCK_MAX_LOG_DAYS."""
    log_dir = _log_dir()
    if not log_dir.is_dir():
        return
    cutoff = datetime.date.today() - datetime.timedelta(days=_max_log_days())
    for path in log_dir.glob("airlock-*.jsonl"):
        m = _LOG_DATE_RE.match(path.name)
        if not m:
            continue
        try:
            file_date = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                path.unlink()
                logger.info("log_cleanup removed=%s", path.name)
            except OSError:
                logger.warning(
                    "log_cleanup failed to remove %s", path.name, exc_info=True
                )


def _rotate_if_oversized(log_path: Path) -> None:
    """Rename the current log file if it exceeds AIRLOCK_MAX_LOG_SIZE_MB."""
    max_bytes = _max_log_size_mb() * 1024 * 1024
    if not log_path.exists() or log_path.stat().st_size < max_bytes:
        return
    suffix = 1
    while True:
        rotated = log_path.with_suffix(f".{suffix}.jsonl")
        if not rotated.exists():
            break
        suffix += 1
    log_path.rename(rotated)
    logger.info("log_rotation %s -> %s", log_path.name, rotated.name)


def _redact_fields() -> list[str]:
    """Return the list of field names to redact from log records."""
    raw = os.getenv("AIRLOCK_LOG_REDACT_FIELDS", "")
    if not raw:
        return []
    return [f.strip() for f in raw.split(",") if f.strip()]


def _redact_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *record* with configured fields replaced by ``[REDACTED]``.

    Fields listed in ``AIRLOCK_LOG_REDACT_FIELDS`` (comma-separated) have
    their values replaced with the string ``"[REDACTED]"``.  Fields not
    present in the record are silently ignored.  When the env var is unset
    or empty, the record is returned unchanged (shallow copy).
    """
    fields = _redact_fields()
    if not fields:
        return record
    redacted = dict(record)
    for field in fields:
        if field in redacted:
            redacted[field] = "[REDACTED]"
    return redacted


def _write_log(record: dict[str, Any]) -> None:
    """Append a JSON record to today's log file.

    Swallows OSError (e.g. disk full) so logging failures never
    propagate up and cause 500 errors on LLM requests.
    """
    try:
        log_dir = _ensure_log_dir()
        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        _rotate_if_oversized(log_path)
        redacted = _redact_record(record)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(redacted, default=_serialize) + "\n")
    except OSError:
        logger.error("Failed to write log record (disk full?)", exc_info=True)


def write_admin_action_record(record: dict[str, Any]) -> dict[str, Any]:
    """Append an admin_action audit record to the JSONL log (CC-8/CC-9).

    The record (already shaped by a StateStore admin mutator) is both the audit
    trail and the channel by which the TUI replica converges (via
    ``StateStore.ingest_jsonl_record``). Returns the record for convenience.
    """
    _write_log(record)
    logger.info(
        "admin_action op=%s actor=%s target=%s",
        record.get("op"),
        record.get("actor"),
        record.get("provider") or record.get("client_id") or record.get("model"),
    )
    return record


def write_precall_block_record(
    data: dict[str, Any],
    *,
    error: str,
    error_type: str,
    failure_category: str = "provider",
) -> dict[str, Any]:
    """Write a structured log record for failures raised before LiteLLM callbacks fire."""
    metadata = data.get("metadata", {}) or {}
    provider = metadata.get("airlock_provider") or infer_provider(
        data.get("model", "unknown")
    )
    guardrail_meta = {
        key: value for key, value in metadata.items() if key.startswith("airlock_")
    }
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "timestamp": now.isoformat(),
        "success": False,
        "model": data.get("model", "unknown"),
        "user": metadata.get("user_api_key_alias")
        or metadata.get("user_api_key_user_id"),
        "team": metadata.get("user_api_key_team_alias"),
        "request_id": metadata.get("request_id"),
        "messages": data.get("messages"),
        "response": None,
        "error": error,
        "error_type": error_type,
        "failure_category": failure_category,
        "airlock_provider": provider,
        "start_time": now,
        "end_time": now,
        "duration_ms": 0,
        **guardrail_meta,
    }
    record["airlock_client"] = _get_airlock_client(
        metadata, {"headers": data.get("headers")}
    )
    _write_log(record)
    logger.warning(
        "request_failed model=%s user=%s client=%s category=%s error_type=%s error=%s",
        record["model"],
        record.get("user"),
        record.get("airlock_client"),
        record.get("failure_category"),
        record.get("error_type"),
        record.get("error"),
    )
    return record


def write_batch_record(
    *,
    event: str,
    batch_id: str,
    provider: str,
    model: str | None,
    status: str,
    row_count: int | None = None,
    input_file_id: str | None = None,
    job_id: str | None = None,
    client: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Write a structured log record for a batch/file job lifecycle event.

    Batch jobs run outside the LiteLLM success/failure callback path, so this
    public writer is how the batch routes emit observability records.  The
    record is tagged ``call_type="batch"`` and ``is_batch_call=True`` so the
    TUI and analyzers can distinguish it from interactive traffic, then routed
    through ``_write_log`` to reuse rotation + redaction.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "timestamp": now.isoformat(),
        "success": error is None,
        "call_type": "batch",
        "is_batch_call": True,
        "event": event,
        "batch_id": batch_id,
        "airlock_provider": provider,
        "model": model,
        "status": status,
        "row_count": row_count,
        "input_file_id": input_file_id,
        "job_id": job_id,
        "airlock_client": client,
        "error": error,
    }
    _write_log(record)
    logger.info(
        "batch_record event=%s batch_id=%s provider=%s status=%s client=%s error=%s",
        event,
        batch_id,
        provider,
        status,
        client,
        error,
    )
    return record


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
    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        record = self._build_record(
            kwargs, response_obj, start_time, end_time, success=True
        )
        _write_log(record)
        logger.info(
            "request_logged model=%s user=%s tokens=%s",
            record["model"],
            record.get("user"),
            record.get("total_tokens"),
        )

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        import asyncio

        await asyncio.to_thread(
            self.log_success_event, kwargs, response_obj, start_time, end_time
        )

    # ------------------------------------------------------------------
    # Failure
    # ------------------------------------------------------------------
    def log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        record = self._build_record(
            kwargs, response_obj, start_time, end_time, success=False
        )
        _write_log(record)
        logger.warning(
            "request_failed model=%s user=%s client=%s category=%s error_type=%s error=%s",
            record["model"],
            record.get("user"),
            record.get("airlock_client"),
            record.get("failure_category"),
            record.get("error_type"),
            record.get("error"),
        )

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        import asyncio

        await asyncio.to_thread(
            self.log_failure_event, kwargs, response_obj, start_time, end_time
        )

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
        litellm_params = kwargs.get("litellm_params", {}) or {}
        metadata = litellm_params.get("metadata", {}) or {}
        airlock_client = _get_airlock_client(metadata, kwargs)
        error = None
        error_type = None
        failure_category = None
        if not success:
            error, error_type, failure_category = _normalize_failure(
                kwargs, response_obj
            )

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
        guardrail_meta = {k: v for k, v in metadata.items() if k.startswith("airlock_")}
        provider = metadata.get("airlock_provider") or infer_provider(
            kwargs.get("model", "unknown")
        )

        if success and is_gemini_provider(kwargs.get("model"), provider):
            request_meta = metadata.get("airlock_gemini") or {
                "mode": "balanced",
                "visibility": "final_only",
                "allow_empty_text": False,
                "mapping_source": "implicit",
                "explicit_controls": [],
                "provider": "gemini",
                "model": kwargs.get("model", "unknown"),
            }
            response_meta = classify_gemini_response(response_obj) or {
                "output_shape": "unknown",
                "empty_text_success": False,
            }
            metadata["airlock_gemini"] = request_meta
            metadata["airlock_gemini_response"] = response_meta
            response_headers = metadata.setdefault("airlock_response_headers", {})
            response_headers.update(
                build_gemini_response_headers(request_meta, response_meta)
            )
            guardrail_meta = {
                k: v for k, v in metadata.items() if k.startswith("airlock_")
            }

        # MCP tool call metadata
        call_type = kwargs.get("call_type", "")
        mcp_meta: dict[str, Any] = {}
        if call_type == "call_mcp_tool" or "mcp_tool_name" in kwargs:
            mcp_meta["call_type"] = call_type or "call_mcp_tool"
            mcp_meta["mcp_tool_name"] = (
                kwargs.get("mcp_tool_name")
                or litellm_params.get("mcp_tool_name")
                or metadata.get("mcp_tool_name")
            )
            mcp_meta["mcp_server_name"] = (
                kwargs.get("mcp_server_name")
                or litellm_params.get("mcp_server_name")
                or metadata.get("mcp_server_name")
            )

        record = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            # CC-9: discriminator so ingest_jsonl_record can route record kinds.
            # Request records are "request"; the admin pack adds "admin_action".
            # An absent record_type is treated as "request" for back-compat.
            "record_type": "request",
            "success": success,
            "model": kwargs.get("model", "unknown"),
            "user": metadata.get("user_api_key_alias")
            or metadata.get("user_api_key_user_id"),
            "team": metadata.get("user_api_key_team_alias"),
            "request_id": kwargs.get("litellm_call_id"),
            "messages": kwargs.get("messages"),
            "response": _serialize(response_obj) if response_obj else None,
            "error": error,
            "error_type": error_type,
            "failure_category": failure_category,
            "airlock_provider": provider,
            "start_time": start_time,
            "end_time": end_time,
            "duration_ms": (
                int((end_time - start_time).total_seconds() * 1000)
                if start_time and end_time
                else None
            ),
            **usage,
            **mcp_meta,
            **guardrail_meta,
        }
        record["airlock_client"] = airlock_client
        return record


# Module-level instance for config.yaml callback registration.
# LiteLLM's get_instance_fn does getattr — it needs an instance, not a class.
# We also self-register into the async callback lists because the proxy runs
# async but config's success_callback key only populates the sync list.
proxy_logger = AirlockLogger()


def _patch_lowest_cost_none_guard() -> None:
    """Monkey-patch LiteLLM lowest_cost router strategy to guard against None litellm_params.

    LiteLLM's LowestCostLoggingHandler.async_log_success_event does
    ``kwargs["litellm_params"].get(...)`` without guarding against None,
    which crashes for call types (MCP, custom providers) that don't
    fully populate litellm_params.
    """
    try:
        from litellm.router_strategy.lowest_cost import LowestCostLoggingHandler

        _orig = LowestCostLoggingHandler.async_log_success_event

        async def _safe_async_log_success(
            self, kwargs, response_obj, start_time, end_time
        ):
            if kwargs.get("litellm_params") is None:
                kwargs["litellm_params"] = {}
            return await _orig(self, kwargs, response_obj, start_time, end_time)

        LowestCostLoggingHandler.async_log_success_event = _safe_async_log_success  # type: ignore[method-assign]
    except Exception:
        logger.warning("lowest_cost_none_guard patch failed", exc_info=True)


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
        logger.warning(
            "enterprise_logger self-registration deferred — litellm not fully loaded",
            exc_info=True,
        )

    _patch_lowest_cost_none_guard()


_self_register()
_cleanup_old_logs()
