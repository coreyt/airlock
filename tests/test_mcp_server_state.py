"""Tests for McpServerState and StateStore MCP server methods."""

from __future__ import annotations

import time

from airlock.fast.state import (
    McpServerHealth,
    McpServerState,
    StateStore,
)


class TestMcpServerHealth:
    """McpServerHealth enum values."""

    def test_enum_values(self):
        assert McpServerHealth.UNKNOWN.value == "unknown"
        assert McpServerHealth.HEALTHY.value == "healthy"
        assert McpServerHealth.UNHEALTHY.value == "unhealthy"
        assert McpServerHealth.STARTING.value == "starting"
        assert McpServerHealth.STOPPED.value == "stopped"


class TestMcpServerState:
    """McpServerState dataclass behaviour."""

    def test_defaults(self):
        s = McpServerState(name="test")
        assert s.name == "test"
        assert s.transport == ""
        assert s.url == ""
        assert s.is_managed is False
        assert s.health == McpServerHealth.UNKNOWN
        assert s.pid == 0
        assert s.started_at == 0.0
        assert s.consecutive_failures == 0
        assert len(s.health_history) == 0

    def test_record_healthy_check(self):
        s = McpServerState(name="srv")
        now = time.time()
        s.record_health_check(now, healthy=True, latency_ms=12.5)

        assert s.health == McpServerHealth.HEALTHY
        assert s.last_health_check == now
        assert s.last_health_latency_ms == 12.5
        assert s.consecutive_failures == 0
        assert len(s.health_history) == 1
        assert s.health_history[0] == (now, True)

    def test_record_unhealthy_check(self):
        s = McpServerState(name="srv")
        now = time.time()
        s.record_health_check(now, healthy=False, latency_ms=5000.0)

        assert s.health == McpServerHealth.UNHEALTHY
        assert s.consecutive_failures == 1

    def test_consecutive_failures_reset_on_success(self):
        s = McpServerState(name="srv")
        now = time.time()
        s.record_health_check(now, healthy=False, latency_ms=0)
        s.record_health_check(now + 1, healthy=False, latency_ms=0)
        assert s.consecutive_failures == 2

        s.record_health_check(now + 2, healthy=True, latency_ms=10)
        assert s.consecutive_failures == 0
        assert s.health == McpServerHealth.HEALTHY

    def test_starting_state_not_overwritten_by_failure(self):
        s = McpServerState(name="srv", health=McpServerHealth.STARTING)
        now = time.time()
        s.record_health_check(now, healthy=False, latency_ms=0)
        # Should stay STARTING, not flip to UNHEALTHY
        assert s.health == McpServerHealth.STARTING
        assert s.consecutive_failures == 1

    def test_uptime_seconds_not_started(self):
        s = McpServerState(name="srv")
        assert s.uptime_seconds() == 0.0

    def test_uptime_seconds_running(self):
        s = McpServerState(name="srv", started_at=time.time() - 60)
        uptime = s.uptime_seconds()
        assert 59 <= uptime <= 61

    def test_recent_success_rate_empty(self):
        s = McpServerState(name="srv")
        assert s.recent_success_rate() == 0.0

    def test_recent_success_rate_mixed(self):
        s = McpServerState(name="srv")
        now = time.time()
        s.record_health_check(now, True, 10)
        s.record_health_check(now + 1, True, 10)
        s.record_health_check(now + 2, False, 0)
        s.record_health_check(now + 3, True, 10)
        assert s.recent_success_rate() == 0.75

    def test_health_history_bounded(self):
        s = McpServerState(name="srv")
        now = time.time()
        for i in range(100):
            s.record_health_check(now + i, True, 1.0)
        # maxlen=50
        assert len(s.health_history) == 50


class TestStateStoreMcpServers:
    """StateStore MCP server methods."""

    def test_get_mcp_server_creates_default(self):
        store = StateStore()
        srv = store.get_mcp_server("test-srv")
        assert srv.name == "test-srv"
        assert srv.health == McpServerHealth.UNKNOWN

    def test_get_mcp_server_returns_same_instance(self):
        store = StateStore()
        s1 = store.get_mcp_server("a")
        s2 = store.get_mcp_server("a")
        assert s1 is s2

    def test_all_mcp_servers_returns_snapshot(self):
        store = StateStore()
        store.get_mcp_server("x")
        store.get_mcp_server("y")
        servers = store.all_mcp_servers()
        assert len(servers) == 2
        assert "x" in servers and "y" in servers

    def test_set_mcp_server_overwrites(self):
        store = StateStore()
        store.get_mcp_server("s")  # creates default
        new_state = McpServerState(
            name="s",
            transport="sse",
            url="http://localhost:3001",
            health=McpServerHealth.HEALTHY,
        )
        store.set_mcp_server("s", new_state)
        assert store.get_mcp_server("s").url == "http://localhost:3001"
        assert store.get_mcp_server("s").health == McpServerHealth.HEALTHY
