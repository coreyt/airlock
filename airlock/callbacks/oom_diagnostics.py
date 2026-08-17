"""Opt-in, aggregate-only high-water diagnostics for a LiteLLM worker.

This module is deliberately inert unless ``AIRLOCK_OOM_DIAGNOSTICS=1``.  It
never serializes request/response objects, headers, model names, exception
strings, or metadata supplied by a client.  The resulting bounded JSONL file
therefore contains allocator and kernel counters only.
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import signal
import threading
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.callbacks.memory import collect_memory_snapshot


def _enabled() -> bool:
    return os.getenv("AIRLOCK_OOM_DIAGNOSTICS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _tracemalloc_enabled() -> bool:
    """Allow a faithful replay to avoid tracing allocator perturbation.

    Native/cgroup counters remain available when this is false.  The default is
    deliberately on for short diagnostic runs, while a long reproduction can
    turn it off and attach a short native profile only at high water.
    """
    return os.getenv(
        "AIRLOCK_OOM_DIAGNOSTICS_TRACEMALLOC", "1"
    ).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _proc_kib_values(path: Path, wanted: set[str]) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            name, _, rest = line.partition(":")
            if name not in wanted:
                continue
            fields = rest.split()
            if fields:
                values[name] = int(fields[0]) * 1024
    except (OSError, ValueError):
        pass
    return values


def _memory_pressure() -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    try:
        for line in Path("/proc/pressure/memory").read_text().splitlines():
            kind, *parts = line.split()
            fields = dict(part.split("=", 1) for part in parts if "=" in part)
            result[kind] = {
                key: float(fields[key])
                for key in ("avg10", "avg60", "avg300")
                if key in fields
            }
    except (OSError, ValueError):
        pass
    return result


class _Mallinfo2(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_size_t)
        for name in (
            "arena",
            "ordblks",
            "smblks",
            "hblks",
            "hblkhd",
            "usmblks",
            "fsmblks",
            "uordblks",
            "fordblks",
            "keepcost",
        )
    ]


def _mallinfo() -> dict[str, int]:
    try:
        libc = ctypes.CDLL(None)
        fn = libc.mallinfo2
        fn.restype = _Mallinfo2
        return {name: int(getattr(fn(), name)) for name, _ in _Mallinfo2._fields_}
    except (AttributeError, OSError):
        return {}


def _malloc_trim() -> bool:
    try:
        fn = ctypes.CDLL(None).malloc_trim
        fn.argtypes = [ctypes.c_size_t]
        fn.restype = ctypes.c_int
        return bool(fn(0))
    except (AttributeError, OSError):
        return False


def _object_type_counts() -> dict[str, Any]:
    """Return type names/counts only; never object values or reprs."""
    try:
        counts = Counter(
            f"{type(obj).__module__}.{type(obj).__qualname__}"
            for obj in gc.get_objects()
        )
    except Exception:
        return {}
    return {
        "objects_total": sum(counts.values()),
        "object_types": len(counts),
        "object_types_top": counts.most_common(12),
    }


def _transport_counts() -> dict[str, int]:
    """Count cached LiteLLM clients/connections without retaining or naming them."""
    result = {"litellm_cached_clients": 0, "httpx_connections": 0}
    try:
        import litellm

        clients = getattr(litellm, "in_memory_llm_clients_cache", None)
        values = list(getattr(clients, "cache_dict", {}).values())
        result["litellm_cached_clients"] = len(values)
        for item in values:
            client = getattr(item, "client", item)
            pool = getattr(getattr(client, "_transport", None), "_pool", None)
            connections = getattr(pool, "_connections", ())
            result["httpx_connections"] += len(connections)
    except Exception:
        pass
    return result


class OOMDiagnostics:
    """Process-local recorder with a bounded, append-atomic JSONL artifact."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._signal_lock = threading.Lock()
        self._sequence = 0
        self._in_flight = 0
        self._records = 0
        self._started = False
        self._tracing_enabled = False
        self._path: Path | None = None

    def _start(self) -> None:
        if self._started or not _enabled():
            return
        with self._lock:
            if self._started:
                return
            directory = Path(
                os.getenv("AIRLOCK_OOM_DIAGNOSTICS_DIR", "/tmp/airlock-oom-diagnostics")
            )
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._path = directory / f"litellm-{os.getpid()}.jsonl"
            if _tracemalloc_enabled():
                tracemalloc.start(1)
                self._tracing_enabled = True
            self._started = True
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGUSR1, self._signal_usr1)
            signal.signal(signal.SIGUSR2, self._signal_usr2)
        self.snapshot("diagnostics_started")

    def _append(self, record: dict[str, Any]) -> None:
        if self._path is None or self._records >= _positive_int(
            "AIRLOCK_OOM_DIAGNOSTICS_MAX_RECORDS", 6000
        ):
            return
        line = (
            json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode()
        # A single O_APPEND write plus this process lock prevents interleaving.
        try:
            with self._lock:
                if self._records >= _positive_int(
                    "AIRLOCK_OOM_DIAGNOSTICS_MAX_RECORDS", 6000
                ):
                    return
                fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    # Also normalize an existing diagnostic artifact.  The
                    # requested create mode is subject to the caller's umask,
                    # and a prior run may have left a less-restrictive file.
                    os.fchmod(fd, 0o600)
                    os.write(fd, line)
                finally:
                    os.close(fd)
                self._records += 1
        except OSError:
            pass

    def snapshot(
        self,
        phase: str,
        *,
        sequence: int | None = None,
        outcome: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        if not _enabled():
            return
        self._start()
        memory = collect_memory_snapshot()
        smaps = _proc_kib_values(
            Path("/proc/self/smaps_rollup"),
            {"Rss", "Pss_Anon", "Private_Clean", "Private_Dirty", "AnonHugePages"},
        )
        trace_current, trace_peak = (
            tracemalloc.get_traced_memory()
            if self._tracing_enabled and tracemalloc.is_tracing()
            else (0, 0)
        )
        with self._lock:
            in_flight = self._in_flight
        self._append(
            {
                "ts_monotonic_ns": time.monotonic_ns(),
                "phase": phase,
                "sequence": sequence,
                "outcome": outcome,
                "elapsed_ms": elapsed_ms,
                "in_flight": in_flight,
                "memory": {
                    "rss": memory.process_rss_bytes,
                    "rss_peak": memory.process_rss_peak_bytes,
                    "cgroup_current": memory.cgroup_current_bytes,
                    "cgroup_peak": memory.cgroup_peak_bytes,
                    "cgroup_high": memory.cgroup_high_bytes,
                    "cgroup_max": memory.cgroup_max_bytes,
                    "events": memory.cgroup_events,
                    "smaps": smaps,
                    "pressure": _memory_pressure(),
                },
                "allocator": _mallinfo(),
                "tracemalloc": {"current": trace_current, "peak": trace_peak},
                "process": {
                    "threads": _count_entries("/proc/self/task"),
                    "fds": _count_entries("/proc/self/fd"),
                },
                # gc.get_objects() walks the full Python heap.  It is useful at
                # periodic/high-water checkpoints but would itself perturb a
                # thousands-request faithful replay if performed three times per
                # request.
                "objects": _object_type_counts()
                if phase == "periodic" or phase.startswith("signal_")
                else {},
                "transport": _transport_counts(),
            }
        )

    def pre_call(self, data: dict) -> None:
        if not _enabled():
            return
        self._start()
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            self._in_flight += 1
        metadata = data.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["airlock_oom_diag_sequence"] = sequence
            metadata["airlock_oom_diag_started_ns"] = time.monotonic_ns()
        self.snapshot("request_entry", sequence=sequence)
        if sequence % _positive_int("AIRLOCK_OOM_DIAGNOSTICS_EVERY", 25) == 0:
            self.snapshot("periodic", sequence=sequence)

    def post_call(self, data: dict) -> None:
        if not _enabled():
            return
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        sequence = (
            metadata.get("airlock_oom_diag_sequence")
            if isinstance(metadata, dict)
            else None
        )
        self.snapshot(
            "provider_response",
            sequence=sequence if isinstance(sequence, int) else None,
        )

    def record_event(self, event: Any) -> None:
        if not _enabled():
            return
        metadata = getattr(event, "guardrail_meta", {}) or {}
        sequence = metadata.get("airlock_oom_diag_sequence")
        started = metadata.get("airlock_oom_diag_started_ns")
        if not isinstance(sequence, int):
            return
        elapsed_ms = (
            (time.monotonic_ns() - started) // 1_000_000
            if isinstance(started, int)
            else None
        )
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
        self.snapshot(
            "callback_complete",
            sequence=sequence,
            outcome="success" if getattr(event, "success", False) else "failure",
            elapsed_ms=elapsed_ms,
        )

    def _signal_usr1(self, _signum: int, _frame: Any) -> None:
        self._signal_snapshot("signal_usr1", trim=False)

    def _signal_usr2(self, _signum: int, _frame: Any) -> None:
        self._signal_snapshot("signal_usr2", trim=True)

    def _record_inflight_trim_skip(self, phase: str, in_flight: int) -> None:
        """Record why a trim was skipped without perturbing the live workload.

        A full signal snapshot enumerates the Python heap.  That work is
        specifically unhelpful while requests are in flight, and can hold the
        signal gate long enough to make a second operator signal look stuck.
        Preserve the bounded audit event while deliberately omitting sampled
        process details.
        """
        self._append(
            {
                "ts_monotonic_ns": time.monotonic_ns(),
                "phase": f"{phase}_skipped_in_flight",
                "sequence": None,
                "in_flight": in_flight,
                "trim": {"attempted": False, "reason": "requests_in_flight"},
            }
        )

    def _signal_snapshot(self, phase: str, *, trim: bool) -> None:
        if not _enabled() or not self._signal_lock.acquire(blocking=False):
            return

        def worker() -> None:
            try:
                with self._lock:
                    in_flight = self._in_flight
                if trim and in_flight:
                    self._record_inflight_trim_skip(phase, in_flight)
                    return
                self.snapshot(f"{phase}_before")
                if trim:
                    gc.collect()
                    trimmed = _malloc_trim()
                    self.snapshot(f"{phase}_after_trim_{int(trimmed)}")
                else:
                    gc.collect()
                    self.snapshot(f"{phase}_after_gc")
            finally:
                self._signal_lock.release()

        threading.Thread(
            target=worker, daemon=True, name="airlock-oom-snapshot"
        ).start()


def _count_entries(path: str) -> int | None:
    try:
        return sum(1 for _ in Path(path).iterdir())
    except OSError:
        return None


oom_diagnostics = OOMDiagnostics()


class AirlockOOMDiagnosticsGuard(CustomGuardrail):
    """No-op guardrail unless diagnostics are explicitly enabled for a repro."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            supported_event_hooks=[
                GuardrailEventHooks.pre_call,
                GuardrailEventHooks.post_call,
            ],
            **kwargs,
        )

    async def async_pre_call_hook(
        self, user_api_key_dict: Any, cache: DualCache, data: dict, call_type: str
    ) -> dict:  # noqa: ARG002
        oom_diagnostics.pre_call(data)
        return data

    async def async_post_call_success_hook(
        self, data: dict, user_api_key_dict: Any, response: Any
    ) -> Any:  # noqa: ARG002
        oom_diagnostics.post_call(data)
        return response
