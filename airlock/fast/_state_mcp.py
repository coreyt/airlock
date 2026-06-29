"""
Airlock Fast — MCP server and tool state sub-module.

Contains McpToolState, McpServerHealth, McpServerState.
Intentionally standalone (no imports from other airlock.fast sub-modules)
so _state_core.py can import from here without a circular dependency.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

# Local mirror of the core constants (same values; state.py re-exports from _state_core).
_MAX_SAMPLES = 1000
_WINDOW_SECONDS = 300


# ---------------------------------------------------------------------------
# Per-MCP-tool state
# ---------------------------------------------------------------------------
@dataclass
class McpToolState:
    """Tracks a single MCP tool's health (modeled after ModelState)."""

    tool_name: str
    server_name: str = ""
    success_times: deque = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))
    failure_times: deque = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=_MAX_SAMPLES))

    def record_success(self, timestamp: float, latency_ms: float) -> None:
        self.success_times.append(timestamp)
        self.latencies_ms.append((timestamp, latency_ms))

    def record_failure(self, timestamp: float) -> None:
        self.failure_times.append(timestamp)

    def recent_avg_latency(
        self, window_seconds: float = _WINDOW_SECONDS
    ) -> float | None:
        cutoff = time.time() - window_seconds
        recent = [lat for t, lat in self.latencies_ms if t > cutoff]
        return sum(recent) / len(recent) if recent else None

    def recent_error_rate(self, window_seconds: float = _WINDOW_SECONDS) -> float:
        cutoff = time.time() - window_seconds
        errors = sum(1 for t in self.failure_times if t > cutoff)
        successes = sum(1 for t in self.success_times if t > cutoff)
        total = errors + successes
        return errors / total if total > 0 else 0.0

    def recent_call_count(self, window_seconds: float = _WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        return sum(1 for t in self.success_times if t > cutoff) + sum(
            1 for t in self.failure_times if t > cutoff
        )


# ---------------------------------------------------------------------------
# Per-MCP-server state
# ---------------------------------------------------------------------------
class McpServerHealth(Enum):
    """MCP server health states."""

    UNKNOWN = "unknown"  # never probed
    HEALTHY = "healthy"  # last probe succeeded
    UNHEALTHY = "unhealthy"  # last probe failed
    STARTING = "starting"  # managed server launching
    STOPPED = "stopped"  # managed server not running


@dataclass
class McpServerState:
    """Tracks an MCP server's health and lifecycle."""

    name: str
    transport: str = ""  # "sse", "http", "stdio"
    url: str = ""
    is_managed: bool = False
    health: McpServerHealth = McpServerHealth.UNKNOWN
    last_health_check: float = 0.0
    last_health_latency_ms: float = 0.0
    consecutive_failures: int = 0
    started_at: float = 0.0  # unix ts; 0 = not started by Airlock
    pid: int = 0  # PID of managed process; 0 = none
    health_history: deque = field(
        default_factory=lambda: deque(maxlen=50),
    )

    def record_health_check(
        self,
        timestamp: float,
        healthy: bool,
        latency_ms: float,
    ) -> None:
        self.last_health_check = timestamp
        self.last_health_latency_ms = latency_ms
        self.health_history.append((timestamp, healthy))
        if healthy:
            self.health = McpServerHealth.HEALTHY
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.health != McpServerHealth.STARTING:
                self.health = McpServerHealth.UNHEALTHY

    def uptime_seconds(self) -> float:
        if self.started_at > 0:
            return time.time() - self.started_at
        return 0.0

    def recent_success_rate(self) -> float:
        """Fraction of recent health checks that succeeded."""
        if not self.health_history:
            return 0.0
        ok = sum(1 for _, healthy in self.health_history if healthy)
        return ok / len(self.health_history)
