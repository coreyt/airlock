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

    def record_event(self, event: Any) -> None:
        """Fathom sink (0.5.4-MIGRATE) — the live fathom path (pack 2b-ii cutover).

        The skip / engine / call_id / dedup / write functional path from the
        canonical event via ``project_fathom``. Registered by the recorder only
        when ``AIRLOCK_ENABLE_FATHOM_LOGGER`` is set, async-only.
        """
        from airlock.callbacks.projections import project_fathom

        if event.guardrail_meta.get("airlock_skip_fathom_logger"):
            return

        db_engine = self._get_engine()
        if not db_engine or WriteRequestBuilder is None:
            return

        call_id = event.request_id or uuid.uuid4().hex
        if self._should_skip_call_id(call_id):
            return

        builder = WriteRequestBuilder("airlock_log")
        builder.add_node(
            row_id=uuid.uuid4().hex,
            logical_id=call_id,
            kind="RequestLog",
            properties=project_fathom(event),
            source_ref="airlock:fathom_logger",
            upsert=True,
        )
        try:
            db_engine.write(builder.build())
        except Exception as e:
            logger.error(f"FathomDB write failed: {e}")


# Module-level instance referenced by the recorder (airlock.callbacks.recorder),
# which registers record_event as an async-only sink when AIRLOCK_ENABLE_FATHOM_LOGGER
# is set. No self-registration into LiteLLM here — the recorder owns dispatch.
proxy_fathom_logger = AirlockFathomLogger()
