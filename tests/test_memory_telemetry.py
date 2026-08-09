"""Tests for production-safe callback memory telemetry."""

from __future__ import annotations

from types import SimpleNamespace

from prometheus_client import CollectorRegistry, Gauge

from airlock.callbacks import memory, metrics
from airlock.callbacks.memory import MemorySnapshot
from airlock.callbacks.metrics import AirlockMetricsCallback


def test_collect_memory_snapshot_reads_process_and_cgroup_counters(monkeypatch):
    files = {
        "/proc/self/status": "VmRSS:\t  1024 kB\nVmHWM:\t 2048 kB\n",
        "/proc/self/cgroup": "0::/user.slice/user-1000.slice/app.slice/airlock.service\n",
        "/proc/self/mountinfo": "36 29 0:32 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
        "/sys/fs/cgroup/user.slice/user-1000.slice/app.slice/airlock.service/memory.current": "3145728\n",
        "/sys/fs/cgroup/user.slice/user-1000.slice/app.slice/airlock.service/memory.peak": "4194304\n",
        "/sys/fs/cgroup/user.slice/user-1000.slice/app.slice/airlock.service/memory.high": "3221225472\n",
        "/sys/fs/cgroup/user.slice/user-1000.slice/app.slice/airlock.service/memory.max": "4294967296\n",
        "/sys/fs/cgroup/user.slice/user-1000.slice/app.slice/airlock.service/memory.events": "high 2\nmax 1\noom 0\noom_kill 0\n",
    }
    monkeypatch.setattr(memory, "_read_text", lambda path: files[str(path)])

    snapshot = memory.collect_memory_snapshot()

    assert snapshot.process_rss_bytes == 1024 * 1024
    assert snapshot.process_rss_peak_bytes == 2048 * 1024
    assert snapshot.cgroup_current_bytes == 3145728
    assert snapshot.cgroup_peak_bytes == 4194304
    assert snapshot.cgroup_high_bytes == 3221225472
    assert snapshot.cgroup_max_bytes == 4294967296
    assert snapshot.cgroup_events == {"high": 2, "max": 1, "oom": 0, "oom_kill": 0}


def test_memory_snapshot_treats_unbounded_cgroup_values_as_none(monkeypatch):
    monkeypatch.setattr(memory, "_read_text", lambda path: "max\n")
    assert memory._memory_value("max\n") is None


def test_callback_records_memory_gauges(monkeypatch):
    registry = CollectorRegistry()
    fresh = {
        "requests_total": Gauge(
            "test_requests_total",
            "requests",
            ["model", "user", "success"],
            registry=registry,
        ),
        "process_resident_memory": Gauge(
            "test_process_resident_memory", "rss", registry=registry
        ),
        "process_resident_memory_peak": Gauge(
            "test_process_resident_memory_peak", "peak", registry=registry
        ),
        "cgroup_memory_current": Gauge(
            "test_cgroup_memory_current", "current", registry=registry
        ),
        "cgroup_memory_peak": Gauge(
            "test_cgroup_memory_peak", "peak", registry=registry
        ),
        "cgroup_memory_high": Gauge(
            "test_cgroup_memory_high", "high", registry=registry
        ),
        "cgroup_memory_max": Gauge("test_cgroup_memory_max", "max", registry=registry),
        "cgroup_memory_events": Gauge(
            "test_cgroup_memory_events", "events", ["event"], registry=registry
        ),
    }
    monkeypatch.setattr(metrics, "_metrics", fresh)
    monkeypatch.setattr(
        metrics,
        "collect_memory_snapshot",
        lambda: MemorySnapshot(10, 20, 30, 40, 50, 60, {"high": 2, "oom_kill": 1}),
    )

    AirlockMetricsCallback().record_event(
        SimpleNamespace(
            success=False,
            model="model",
            user=None,
            start_time=None,
            end_time=None,
            mutations=[],
        )
    )

    assert fresh["process_resident_memory"]._value.get() == 10
    assert fresh["process_resident_memory_peak"]._value.get() == 20
    assert fresh["cgroup_memory_current"]._value.get() == 30
    assert fresh["cgroup_memory_peak"]._value.get() == 40
    assert fresh["cgroup_memory_high"]._value.get() == 50
    assert fresh["cgroup_memory_max"]._value.get() == 60
    assert fresh["cgroup_memory_events"].labels(event="high")._value.get() == 2
    assert fresh["cgroup_memory_events"].labels(event="oom_kill")._value.get() == 1
