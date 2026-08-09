"""Low-overhead memory snapshots for the LiteLLM callback process.

The recorder runs in the LiteLLM worker process, so ``/proc/self`` measures the
same process that can exhaust the service cgroup.  Snapshots deliberately read
only kernel-maintained counters: they do not inspect request content, retain
payloads, or enable allocation tracing in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemorySnapshot:
    """Best-effort process and cgroup memory counters, in bytes."""

    process_rss_bytes: int | None
    process_rss_peak_bytes: int | None
    cgroup_current_bytes: int | None
    cgroup_peak_bytes: int | None
    cgroup_high_bytes: int | None
    cgroup_max_bytes: int | None
    cgroup_events: dict[str, int]


def _read_text(path: Path) -> str:
    return path.read_text()


def _memory_value(text: str) -> int | None:
    """Parse a cgroup value, treating the ``max`` sentinel as unbounded."""
    value = text.strip()
    if not value or value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _status_value(status: str, key: str) -> int | None:
    """Read a ``VmRSS``/``VmHWM`` value from ``/proc/self/status`` in bytes."""
    prefix = f"{key}:"
    for line in status.splitlines():
        if not line.startswith(prefix):
            continue
        fields = line.split()
        if len(fields) < 2:
            return None
        try:
            return int(fields[1]) * 1024  # Linux reports these values in KiB.
        except ValueError:
            return None
    return None


def _cgroup_v2_directory() -> Path | None:
    """Resolve this process's cgroup-v2 directory from procfs mount metadata."""
    try:
        cgroup_path = next(
            line.split("::", 1)[1]
            for line in _read_text(Path("/proc/self/cgroup")).splitlines()
            if "::" in line
        )
        for line in _read_text(Path("/proc/self/mountinfo")).splitlines():
            before, separator, after = line.partition(" - ")
            if not separator or after.split()[0:1] != ["cgroup2"]:
                continue
            fields = before.split()
            if len(fields) < 5:
                continue
            root, mount_point = fields[3], fields[4]
            if root != "/" and not cgroup_path.startswith(root.rstrip("/") + "/"):
                continue
            relative = (
                cgroup_path[len(root) :].lstrip("/")
                if root != "/"
                else cgroup_path.lstrip("/")
            )
            return Path(mount_point, relative)
    except (OSError, StopIteration):
        return None
    return None


def _cgroup_events(cgroup_dir: Path) -> dict[str, int]:
    try:
        pairs = (
            line.split(maxsplit=1)
            for line in _read_text(cgroup_dir / "memory.events").splitlines()
        )
        return {
            name: int(value)
            for name, value in pairs
            if name in {"high", "max", "oom", "oom_kill"}
        }
    except (OSError, ValueError):
        return {}


def collect_memory_snapshot() -> MemorySnapshot:
    """Return a best-effort, no-throw snapshot for a completed LiteLLM callback."""
    try:
        status = _read_text(Path("/proc/self/status"))
    except OSError:
        status = ""

    cgroup_dir = _cgroup_v2_directory()
    cgroup_values: dict[str, int | None] = {}
    if cgroup_dir is not None:
        for name in ("memory.current", "memory.peak", "memory.high", "memory.max"):
            try:
                cgroup_values[name] = _memory_value(_read_text(cgroup_dir / name))
            except OSError:
                cgroup_values[name] = None

    return MemorySnapshot(
        process_rss_bytes=_status_value(status, "VmRSS"),
        process_rss_peak_bytes=_status_value(status, "VmHWM"),
        cgroup_current_bytes=cgroup_values.get("memory.current"),
        cgroup_peak_bytes=cgroup_values.get("memory.peak"),
        cgroup_high_bytes=cgroup_values.get("memory.high"),
        cgroup_max_bytes=cgroup_values.get("memory.max"),
        cgroup_events=_cgroup_events(cgroup_dir) if cgroup_dir is not None else {},
    )
