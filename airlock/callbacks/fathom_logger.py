from __future__ import annotations

from datetime import datetime
import json
import logging
import os
import threading
import time
import uuid
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

try:
    from fathomdb import WriteRequestBuilder
except ImportError:
    WriteRequestBuilder = None

logger = logging.getLogger("airlock.logger")


def _debug_enabled() -> bool:
    return os.getenv("AIRLOCK_DEBUG_FATHOM_LOGGER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _serialize(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return str(obj)


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(_serialize(value), default=_serialize, ensure_ascii=False)


def _response_text(response_obj: Any) -> str | None:
    if response_obj is None:
        return None
    try:
        choices = getattr(response_obj, "choices", None) or []
        if not choices:
            return None
        message = getattr(choices[0], "message", None)
        if message is None and isinstance(choices[0], dict):
            message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if content is None:
            return None
        if isinstance(content, list):
            return _json_text(content)
        return str(content)
    except Exception:
        return None


def _base_record(
    kwargs: dict,
    response_obj: Any,
    start_time: Any,
    end_time: Any,
    *,
    success: bool,
) -> dict[str, Any]:
    from airlock.callbacks.enterprise_logger import AirlockLogger

    return AirlockLogger._build_record(
        kwargs,
        response_obj,
        start_time,
        end_time,
        success=success,
    )


def _fathom_properties(
    kwargs: dict,
    response_obj: Any,
    start_time: Any,
    end_time: Any,
    *,
    success: bool,
) -> dict[str, Any]:
    record = _base_record(
        kwargs,
        response_obj,
        start_time,
        end_time,
        success=success,
    )
    properties: dict[str, Any] = {
        "timestamp": record.get("timestamp"),
        "success": record.get("success"),
        "error_flag": not success,
        "model": record.get("model"),
        "airlock_provider": record.get("airlock_provider"),
        "request_id": record.get("request_id"),
        "call_id": record.get("request_id"),
        "prompt_tokens": record.get("prompt_tokens", 0),
        "completion_tokens": record.get("completion_tokens", 0),
        "total_tokens": record.get("total_tokens", 0),
        "cost": kwargs.get("response_cost", 0),
        "duration_ms": record.get("duration_ms"),
        "failure_category": record.get("failure_category"),
        "call_type": record.get("call_type"),
        "mcp_tool_name": record.get("mcp_tool_name"),
        "mcp_server_name": record.get("mcp_server_name"),
    }

    if _env_flag("AIRLOCK_FATHOM_STORE_CLIENT"):
        properties["airlock_client"] = record.get("airlock_client")

    if _env_flag("AIRLOCK_FATHOM_STORE_USER_TEAM"):
        properties["user"] = record.get("user")
        properties["team"] = record.get("team")

    if _env_flag("AIRLOCK_FATHOM_STORE_ERROR_DETAILS"):
        properties["error_type"] = record.get("error_type")
        properties["error"] = record.get("error")

    if _env_flag("AIRLOCK_FATHOM_STORE_MESSAGES"):
        properties["messages_json"] = _json_text(record.get("messages"))

    if _env_flag("AIRLOCK_FATHOM_STORE_RESPONSE_TEXT"):
        properties["response_text"] = _response_text(response_obj)

    if _env_flag("AIRLOCK_FATHOM_STORE_HEADERS"):
        properties["headers_json"] = _json_text(kwargs.get("headers"))

    if _env_flag("AIRLOCK_FATHOM_STORE_MCP_PAYLOADS"):
        mcp_arguments = (
            kwargs.get("mcp_arguments")
            or ((kwargs.get("litellm_params") or {}).get("mcp_arguments"))
            or (
                ((kwargs.get("litellm_params") or {}).get("metadata") or {}).get(
                    "mcp_arguments"
                )
            )
        )
        properties["mcp_arguments_json"] = _json_text(mcp_arguments)

    return {key: value for key, value in properties.items() if value is not None}


class AirlockFathomLogger(CustomLogger):
    """LiteLLM callback that records logical Airlock requests in FathomDB.

    Parameters
    ----------
    engine : Any, optional
        Pre-opened Fathom engine to use instead of Airlock datastore
        singleton. Primarily useful for tests.
    """

    def __init__(self, engine: Any = None):
        super().__init__()
        self.engine = engine
        self._seen_call_ids: dict[str, float] = {}
        self._seen_call_ids_lock = threading.Lock()

    def _should_skip_call_id(self, call_id: str) -> bool:
        now = time.monotonic()
        with self._seen_call_ids_lock:
            expired = [
                key
                for key, timestamp in self._seen_call_ids.items()
                if now - timestamp > 300
            ]
            for key in expired:
                self._seen_call_ids.pop(key, None)

            if call_id in self._seen_call_ids:
                return True

            self._seen_call_ids[call_id] = now

            if len(self._seen_call_ids) > 4096:
                oldest_key = min(self._seen_call_ids, key=self._seen_call_ids.get)
                self._seen_call_ids.pop(oldest_key, None)

            return False

    def _get_engine(self) -> Any | None:
        if self.engine:
            return self.engine
        import airlock.datastore

        return airlock.datastore.get_engine()

    def _log_event(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
        error_flag: bool,
    ) -> None:
        metadata = (kwargs.get("litellm_params") or {}).get("metadata") or {}
        if metadata.get("airlock_skip_fathom_logger"):
            if _debug_enabled():
                logger.warning(
                    "Fathom debug skip metadata error_flag=%s model=%s call_id=%s thread=%s",
                    error_flag,
                    kwargs.get("model", "unknown"),
                    kwargs.get("litellm_call_id"),
                    threading.current_thread().name,
                )
            return

        db_engine = self._get_engine()
        if not db_engine or WriteRequestBuilder is None:
            if _debug_enabled():
                logger.warning(
                    "Fathom debug skip engine=%s builder=%s error_flag=%s model=%s call_id=%s thread=%s",
                    bool(db_engine),
                    WriteRequestBuilder is not None,
                    error_flag,
                    kwargs.get("model", "unknown"),
                    kwargs.get("litellm_call_id"),
                    threading.current_thread().name,
                )
            return

        call_id = kwargs.get("litellm_call_id") or uuid.uuid4().hex
        if self._should_skip_call_id(call_id):
            if _debug_enabled():
                logger.warning(
                    "Fathom debug skip duplicate error_flag=%s model=%s call_id=%s thread=%s",
                    error_flag,
                    kwargs.get("model", "unknown"),
                    call_id,
                    threading.current_thread().name,
                )
            return

        builder = WriteRequestBuilder("airlock_log")
        builder.add_node(
            row_id=uuid.uuid4().hex,
            logical_id=call_id,
            kind="RequestLog",
            properties=_fathom_properties(
                kwargs,
                response_obj,
                start_time,
                end_time,
                success=not error_flag,
            ),
            source_ref="airlock:fathom_logger",
            upsert=True,
        )
        try:
            receipt = db_engine.write(builder.build())
            if _debug_enabled():
                properties = _fathom_properties(
                    kwargs,
                    response_obj,
                    start_time,
                    end_time,
                    success=not error_flag,
                )
                logger.warning(
                    "Fathom debug wrote error_flag=%s model=%s call_id=%s tokens=%s cost=%s receipt=%s thread=%s",
                    error_flag,
                    properties.get("model", "unknown"),
                    call_id,
                    properties.get("total_tokens", 0),
                    properties.get("cost", 0),
                    receipt,
                    threading.current_thread().name,
                )
        except Exception as e:
            logger.error(f"FathomDB write failed: {e}")

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Log successful logical request to FathomDB.

        Parameters
        ----------
        kwargs : dict
            LiteLLM callback kwargs.
        response_obj : Any
            LiteLLM response object.
        start_time : Any
            Callback start timestamp.
        end_time : Any
            Callback end timestamp.
        """
        self._log_event(kwargs, response_obj, start_time, end_time, error_flag=False)

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Log successful logical request from LiteLLM async callback path.

        Parameters
        ----------
        kwargs : dict
            LiteLLM callback kwargs.
        response_obj : Any
            LiteLLM response object.
        start_time : Any
            Callback start timestamp.
        end_time : Any
            Callback end timestamp.
        """
        import asyncio

        await asyncio.to_thread(
            self.log_success_event, kwargs, response_obj, start_time, end_time
        )

    def log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Log failed logical request to FathomDB.

        Parameters
        ----------
        kwargs : dict
            LiteLLM callback kwargs.
        response_obj : Any
            LiteLLM response object.
        start_time : Any
            Callback start timestamp.
        end_time : Any
            Callback end timestamp.
        """
        self._log_event(kwargs, response_obj, start_time, end_time, error_flag=True)

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Log failed logical request from LiteLLM async callback path.

        Parameters
        ----------
        kwargs : dict
            LiteLLM callback kwargs.
        response_obj : Any
            LiteLLM response object.
        start_time : Any
            Callback start timestamp.
        end_time : Any
            Callback end timestamp.
        """
        import asyncio

        await asyncio.to_thread(
            self.log_failure_event, kwargs, response_obj, start_time, end_time
        )


proxy_fathom_logger = AirlockFathomLogger()


def _self_register_async() -> None:
    """Ensure proxy callback reaches LiteLLM's async callback lists.

    LiteLLM's `success_callback` / `failure_callback` config entries populate the
    sync lists, but async proxy requests only invoke `async_log_success_event()`
    and `async_log_failure_event()` from the async lists. Registering the
    module-level instance here matches the pattern used by Airlock's other proxy
    callbacks without double-writing the sync path.
    """
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_async_success_callback(proxy_fathom_logger)
        mgr.add_litellm_async_failure_callback(proxy_fathom_logger)
    except Exception:
        if _debug_enabled():
            logger.warning("Fathom debug async self-register deferred", exc_info=True)


_self_register_async()
