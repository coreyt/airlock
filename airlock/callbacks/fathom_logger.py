from __future__ import annotations

import logging
import uuid
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

try:
    from fathomdb import WriteRequestBuilder
except ImportError:
    WriteRequestBuilder = None

logger = logging.getLogger("airlock.logger")


class AirlockFathomLogger(CustomLogger):
    """LiteLLM callback that logs requests/responses to FathomDB."""

    def __init__(self, engine: Any = None):
        self.engine = engine

    def _get_engine(self) -> Any | None:
        if self.engine:
            return self.engine
        import airlock.datastore

        return airlock.datastore.engine

    def _log_event(self, kwargs: dict, response_obj: Any, error_flag: bool) -> None:
        db_engine = self._get_engine()
        if not db_engine or WriteRequestBuilder is None:
            return

        model = kwargs.get("model", "unknown")
        total_tokens = 0
        if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
            total_tokens = getattr(response_obj.usage, "total_tokens", 0)

        cost = kwargs.get("response_cost", 0)
        call_id = kwargs.get("litellm_call_id") or uuid.uuid4().hex

        builder = WriteRequestBuilder("airlock_log")
        builder.add_node(
            row_id=call_id,
            logical_id=call_id,
            kind="RequestLog",
            properties={
                "model": model,
                "total_tokens": total_tokens,
                "cost": cost,
                "error_flag": error_flag,
                "call_id": call_id,
            },
        )
        try:
            db_engine.write(builder.build())
        except Exception as e:
            logger.error(f"FathomDB write failed: {e}")

    def log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self._log_event(kwargs, response_obj, error_flag=False)

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        import asyncio

        await asyncio.to_thread(
            self.log_success_event, kwargs, response_obj, start_time, end_time
        )

    def log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self._log_event(kwargs, response_obj, error_flag=True)

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        import asyncio

        await asyncio.to_thread(
            self.log_failure_event, kwargs, response_obj, start_time, end_time
        )


proxy_fathom_logger = AirlockFathomLogger()


def _self_register() -> None:
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_success_callback(proxy_fathom_logger)
        mgr.add_litellm_failure_callback(proxy_fathom_logger)
        mgr.add_litellm_async_success_callback(proxy_fathom_logger)
        mgr.add_litellm_async_failure_callback(proxy_fathom_logger)
    except Exception:
        pass


_self_register()
