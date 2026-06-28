"""LIVE module-level recorder wiring (0.5.4-MIGRATE-entfathom-cutover, pack 2b-ii).

Builds the live ``RequestRecorder`` + ``RequestRecorderCallback`` mechanism, registers
the enterprise (always-on) and fathom (async_only, env-gated) sinks, and installs
``recorder_callback`` into LiteLLM's callback lists as the SINGLE live telemetry
callback in enterprise's old slot.

Ordering invariant: ``config.yaml`` lists ``recorder_callback`` BEFORE
``proxy_monitor`` and this module ``_self_register()`` appends to all four LiteLLM
callback lists at import — so the recorder builds the event (snapshotting
``guardrail_meta``) before monitor's ``log_failure_event`` mutates the metadata with
``airlock_provider_protection``. Fathom dispatch is owned here (gated by
``AIRLOCK_ENABLE_FATHOM_LOGGER``), replacing proxy.py's old fathom-callback append.
"""

from __future__ import annotations

import logging

from airlock.callbacks.enterprise_logger import proxy_logger
from airlock.callbacks.fathom_logger import _env_flag, proxy_fathom_logger
from airlock.callbacks.metrics import metrics_callback
from airlock.callbacks.request_event import RequestRecorder, RequestRecorderCallback

logger = logging.getLogger("airlock.logger")


def _build_recorder() -> RequestRecorder:
    """Build the recorder with the enterprise sink always-on (FIRST) and the fathom
    sink registered only when ``AIRLOCK_ENABLE_FATHOM_LOGGER`` is set (async-only)."""
    recorder = RequestRecorder()
    recorder.register(proxy_logger.record_event, name="enterprise")  # always-on, first
    # metrics is always-on too (a normal success+failure sink): the per-request
    # Prometheus counters dispatch through the recorder, not LiteLLM's callback lists.
    recorder.register(metrics_callback.record_event, name="metrics")
    if _env_flag("AIRLOCK_ENABLE_FATHOM_LOGGER", default=False):
        recorder.register(
            proxy_fathom_logger.record_event, name="fathom", async_only=True
        )
    if _env_flag("AIRLOCK_ENABLE_S3_LOGGER", default=False):
        from airlock.callbacks.s3_logger import proxy_s3_logger

        # normal sink (success+failure; NOT async_only) — the AIRLOCK_S3_BUCKET
        # write-gate still discards when no bucket is configured.
        recorder.register(proxy_s3_logger.record_event, name="s3")
    if _env_flag("AIRLOCK_ENABLE_SQL_LOGGER", default=False):
        from airlock.callbacks.sql_logger import proxy_sql_logger

        # normal sink (success+failure; NOT async_only) — the AIRLOCK_SQL_URL
        # disabled-path still no-ops when no connection string is configured.
        recorder.register(proxy_sql_logger.record_event, name="sql")
    return recorder


request_recorder = _build_recorder()
recorder_callback = RequestRecorderCallback(request_recorder)


def _self_register() -> None:
    """Install ``recorder_callback`` into both sync and async LiteLLM callback lists.

    Mirrors the slot the enterprise ``proxy_logger`` used to occupy: config.yaml
    populates the sync lists, but async proxy requests only invoke the async
    callbacks, so the recorder must reach all four lists.
    """
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_success_callback(recorder_callback)
        mgr.add_litellm_failure_callback(recorder_callback)
        mgr.add_litellm_async_success_callback(recorder_callback)
        mgr.add_litellm_async_failure_callback(recorder_callback)
    except Exception:
        logger.warning(
            "recorder self-registration deferred — litellm not fully loaded",
            exc_info=True,
        )


_self_register()
