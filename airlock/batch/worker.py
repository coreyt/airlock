"""Async scan worker — schedules + drives the content-scan off the event loop.

The CPU-bound scan (Presidio NER over up to 1M rows) must not block the proxy's
event loop (design A1). ``schedule_scan`` fires a background task; ``run_scan``
claims the file (race-free), runs the blocking :func:`airlock.batch.scan.scan_file`
in a **thread pool**, and records the terminal state. ``await_file_ready`` lets
the create path wait, bounded, for an in-flight scan to finish.

A thread pool (not a process pool) is the deliberate MVP choice — it keeps the
loop responsive without the fork/re-import/spaCy-reload cost; ``scan_file`` is
executor-agnostic so a ``ProcessPoolExecutor`` swap is one line later.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from airlock.batch import scan
from airlock.batch.store import (
    FILE_FAILED,
    FILE_READY,
    FILE_REJECTED,
    BatchStore,
)

logger = logging.getLogger("airlock.batch")

_TERMINAL_FILE_STATES = {FILE_READY, FILE_REJECTED, FILE_FAILED}

_executor: ThreadPoolExecutor | None = None
# Strong refs to in-flight tasks so the loop does not GC them mid-scan.
_tasks: set[asyncio.Task] = set()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        workers = max(1, int(os.getenv("AIRLOCK_BATCH_SCAN_WORKERS", "2")))
        _executor = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="airlock-batch-scan"
        )
    return _executor


def _record_file(event: str, file_id: str, *, status: str, **extra) -> None:
    """Best-effort scan observability via Pack B's writer (never raises)."""
    try:
        from airlock.callbacks.enterprise_logger import (  # noqa: PLC0415
            write_batch_record,
        )

        write_batch_record(
            event=event,
            batch_id=file_id,
            provider="",
            status=status,
            input_file_id=file_id,
            **extra,
        )
    except Exception:  # noqa: BLE001  observability must never break the scan
        logger.debug(
            "write_batch_record failed for scan event=%s", event, exc_info=True
        )


async def run_scan(
    store: BatchStore,
    file_id: str,
    input_path: str,
    output_path: str,
    profile: dict,
) -> None:
    """Claim the file then scan it in the thread pool, recording the outcome."""
    if not store.claim_file_scan(file_id):
        # Another scheduler owns it, or it is already terminal — nothing to do.
        return

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            _get_executor(), scan.scan_file, input_path, output_path, profile
        )
    except asyncio.CancelledError:
        # Loop shutdown mid-scan: drive the row to a terminal state so it is not
        # stranded SCANNING (no reconciliation loop re-issues scans on restart),
        # then re-raise to honor cancellation.
        store.set_file_failed(file_id, error="scan cancelled (shutdown)")
        raise
    except Exception as exc:  # noqa: BLE001  surface as FAILED, never crash the loop
        logger.exception("batch scan failed for %s", file_id)
        store.set_file_failed(file_id, error=str(exc))
        _record_file("batch_file_scan_failed", file_id, status="failed", error=str(exc))
        return

    if result.status == scan.READY:
        store.set_file_ready(file_id, row_count=result.row_count)
        _record_file(
            "batch_file_ready", file_id, status="ready", row_count=result.row_count
        )
    else:
        store.set_file_rejected(file_id, reason=result.reason or "rejected")
        _record_file(
            "batch_file_rejected", file_id, status="rejected", error=result.reason
        )


def schedule_scan(
    store: BatchStore,
    file_id: str,
    input_path: str,
    output_path: str,
    profile: dict,
) -> asyncio.Task:
    """Fire the scan as a tracked background task (returns immediately)."""
    task = asyncio.create_task(
        run_scan(store, file_id, input_path, output_path, profile)
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def await_file_ready(
    store: BatchStore,
    file_id: str,
    *,
    timeout: float,
    interval: float = 0.02,
) -> dict | None:
    """Poll the file row until terminal or ``timeout`` (design §7.1 create gate).

    Returns the row when it reaches a terminal state (READY/REJECTED/FAILED), the
    last-seen (still-SCANNING) row on timeout, or ``None`` if no such file.
    """
    deadline = time.monotonic() + timeout
    while True:
        row = store.get_file(file_id)
        if row is None:
            return None
        if row.get("status") in _TERMINAL_FILE_STATES:
            return row
        if time.monotonic() >= deadline:
            return row
        await asyncio.sleep(interval)
