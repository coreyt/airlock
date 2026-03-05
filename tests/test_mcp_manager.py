"""Tests for McpServerManager — config loading, health probes, lifecycle."""

from __future__ import annotations

import collections
import os
import queue
import subprocess
import textwrap
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from airlock.fast.state import McpServerHealth, McpServerState, StateStore, store
from airlock.tui.mcp_manager import (
    McpServerEntry,
    McpServerManager,
    _classify_transport,
    probe_http,
    _resolve_env_value,
    _resolve_health_url,
)


# ---------------------------------------------------------------------------
# Helper config fixtures
# ---------------------------------------------------------------------------

_SAMPLE_CONFIG = textwrap.dedent("""\
    mcp_servers:
      - name: remote-sse
        url: http://localhost:3001/sse

      - name: remote-http
        url: http://localhost:3002/mcp

      - name: local-ado
        url: http://localhost:3003/sse
        airlock_managed:
          command: node
          args: ["dist/index.js"]
          cwd: /tmp
          env:
            TOKEN: os.environ/ADO_TOKEN
          health_url: http://localhost:3003/health

      - name: stdio-search
        command: npx
        args: ["-y", "@anthropic/mcp-search"]
""")


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(_SAMPLE_CONFIG)
    return p


@pytest.fixture(autouse=True)
def _clean_store():
    """Reset the global StateStore MCP servers between tests."""
    original = dict(store._mcp_servers)
    store._mcp_servers.clear()
    yield
    store._mcp_servers.clear()
    store._mcp_servers.update(original)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

class TestClassifyTransport:
    def test_sse(self):
        assert _classify_transport({"url": "http://localhost/sse"}) == "sse"

    def test_http(self):
        assert _classify_transport({"url": "http://localhost/mcp"}) == "http"

    def test_stdio(self):
        assert _classify_transport({"command": "npx"}) == "stdio"

    def test_unknown(self):
        assert _classify_transport({}) == "unknown"


class TestResolveEnvValue:
    def test_literal(self):
        assert _resolve_env_value("hello") == "hello"

    def test_env_var(self):
        with patch.dict(os.environ, {"MY_VAR": "secret"}):
            assert _resolve_env_value("os.environ/MY_VAR") == "secret"

    def test_missing_env_var(self):
        assert _resolve_env_value("os.environ/NONEXISTENT_VAR_XYZ") == ""


class TestResolveHealthUrl:
    def test_managed_health_url(self):
        cfg = {"url": "http://a.com/sse"}
        managed = {"health_url": "http://a.com/health"}
        assert _resolve_health_url(cfg, managed) == "http://a.com/health"

    def test_fallback_to_url(self):
        cfg = {"url": "http://a.com/sse"}
        assert _resolve_health_url(cfg, None) == "http://a.com/sse"

    def test_no_url(self):
        assert _resolve_health_url({}, None) == ""


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_loads_all_servers(self, config_file: Path):
        mgr = McpServerManager()
        names = mgr.load_config(config_file)
        assert sorted(names) == ["local-ado", "remote-http", "remote-sse", "stdio-search"]

    def test_classifies_transports(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        assert mgr.get_entry("remote-sse").transport == "sse"
        assert mgr.get_entry("remote-http").transport == "http"
        assert mgr.get_entry("local-ado").transport == "sse"
        assert mgr.get_entry("stdio-search").transport == "stdio"

    def test_identifies_managed(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        assert mgr.is_managed("local-ado") is True
        assert mgr.is_managed("remote-sse") is False
        assert mgr.is_managed("stdio-search") is False

    def test_seeds_state_store(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        srv = store.get_mcp_server("local-ado")
        assert srv.is_managed is True
        assert srv.transport == "sse"

    def test_no_config_file(self, tmp_path: Path):
        mgr = McpServerManager()
        names = mgr.load_config(tmp_path / "nonexistent.yaml")
        assert names == []

    def test_empty_config(self, tmp_path: Path):
        p = tmp_path / "config.yaml"
        p.write_text("general_settings:\n  master_key: test\n")
        mgr = McpServerManager()
        names = mgr.load_config(p)
        assert names == []

    def test_health_url_from_managed(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        entry = mgr.get_entry("local-ado")
        assert entry.health_url == "http://localhost:3003/health"

    def test_health_url_fallback(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        entry = mgr.get_entry("remote-sse")
        assert entry.health_url == "http://localhost:3001/sse"


# ---------------------------------------------------------------------------
# Health probing
# ---------------------------------------------------------------------------

class TestProbeServer:
    def testprobe_http_healthy(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        with patch("airlock.tui.mcp_manager.probe_http", return_value=(True, 12.5)):
            healthy, latency = mgr.probe_server("remote-sse")
        assert healthy is True
        assert latency == 12.5

    def testprobe_http_unhealthy(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        with patch("airlock.tui.mcp_manager.probe_http", return_value=(False, 5000.0)):
            healthy, _ = mgr.probe_server("remote-sse")
        assert healthy is False

    def test_probe_stdio_binary_exists(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        with patch("shutil.which", return_value="/usr/bin/npx"):
            healthy, _ = mgr.probe_server("stdio-search")
        assert healthy is True

    def test_probe_stdio_binary_missing(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        with patch("shutil.which", return_value=None):
            healthy, _ = mgr.probe_server("stdio-search")
        assert healthy is False

    def test_probe_unknown_server(self):
        mgr = McpServerManager()
        healthy, latency = mgr.probe_server("nonexistent")
        assert healthy is False

    def test_probe_all_updates_state(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        with patch("airlock.tui.mcp_manager.probe_http", return_value=(True, 10.0)), \
             patch("shutil.which", return_value="/usr/bin/npx"):
            results = mgr.probe_all()
        assert len(results) == 4
        srv = store.get_mcp_server("remote-sse")
        assert srv.health == McpServerHealth.HEALTHY


# ---------------------------------------------------------------------------
# Lifecycle management
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_start_server_not_managed(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        err = mgr.start_server("remote-sse")
        assert err is not None
        assert "not airlock-managed" in err

    def test_start_server_unknown(self):
        mgr = McpServerManager()
        err = mgr.start_server("nonexistent")
        assert "Unknown server" in err

    def test_start_server_bad_cwd(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        # Override cwd to nonexistent dir
        entry = mgr.get_entry("local-ado")
        entry.managed_config["cwd"] = "/nonexistent/path/xyz"
        err = mgr.start_server("local-ado")
        assert "cwd does not exist" in err

    def test_start_server_success(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_proc.stdout = StringIO("")

        with patch("subprocess.Popen", return_value=mock_proc):
            err = mgr.start_server("local-ado")

        assert err is None
        assert mgr.is_running("local-ado") is True
        srv = store.get_mcp_server("local-ado")
        assert srv.pid == 12345
        assert srv.health == McpServerHealth.STARTING

    def test_start_server_already_running(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 99
        mock_proc.poll.return_value = None
        mock_proc.stdout = StringIO("")

        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start_server("local-ado")
            err = mgr.start_server("local-ado")

        assert "already running" in err

    def test_stop_server(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 100
        mock_proc.poll.return_value = None
        mock_proc.stdout = StringIO("")
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start_server("local-ado")

        mgr.stop_server("local-ado")
        mock_proc.terminate.assert_called_once()
        assert mgr.is_running("local-ado") is False
        srv = store.get_mcp_server("local-ado")
        assert srv.health == McpServerHealth.STOPPED
        assert srv.pid == 0

    def test_stop_server_timeout_kills(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 101
        mock_proc.poll.return_value = None
        mock_proc.stdout = StringIO("")
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired("cmd", 5), 0]

        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start_server("local-ado")

        mgr.stop_server("local-ado")
        mock_proc.kill.assert_called_once()

    def test_restart_server(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 200
        mock_proc.poll.return_value = None
        mock_proc.stdout = StringIO("")
        mock_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start_server("local-ado")
            err = mgr.restart_server("local-ado")

        assert err is None

    def test_start_server_popen_fails(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)

        with patch("subprocess.Popen", side_effect=OSError("No such file")):
            err = mgr.start_server("local-ado")

        assert "failed to start" in err
        srv = store.get_mcp_server("local-ado")
        assert srv.health == McpServerHealth.STOPPED


# ---------------------------------------------------------------------------
# Reader loop and ring buffer
# ---------------------------------------------------------------------------

class TestReaderLoop:
    def test_tees_to_ring_and_queue(self):
        entry = McpServerEntry(
            name="test",
            config={},
            transport="sse",
        )
        mock_proc = MagicMock()
        mock_proc.stdout = StringIO("line1\nline2\nline3\n")
        entry.process = mock_proc

        mgr = McpServerManager()
        mgr._reader_loop(entry)

        assert list(entry.ring) == ["line1", "line2", "line3"]
        lines = []
        while not entry.output_queue.empty():
            lines.append(entry.output_queue.get_nowait())
        assert lines == ["line1", "line2", "line3"]

    def test_tees_to_log_file(self, tmp_path: Path):
        log_file = tmp_path / "test.log"
        entry = McpServerEntry(
            name="test",
            config={},
            transport="sse",
        )
        entry.log_file = open(log_file, "w", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.stdout = StringIO("hello\nworld\n")
        entry.process = mock_proc

        mgr = McpServerManager()
        mgr._reader_loop(entry)
        entry.log_file.close()

        assert log_file.read_text() == "hello\nworld\n"

    def test_ring_bounded(self):
        entry = McpServerEntry(
            name="test",
            config={},
            transport="sse",
        )
        # Override ring with smaller maxlen
        entry.ring = collections.deque(maxlen=3)
        mock_proc = MagicMock()
        mock_proc.stdout = StringIO("a\nb\nc\nd\ne\n")
        entry.process = mock_proc

        mgr = McpServerManager()
        mgr._reader_loop(entry)

        assert list(entry.ring) == ["c", "d", "e"]


# ---------------------------------------------------------------------------
# Crash detection
# ---------------------------------------------------------------------------

class TestCheckCrashes:
    def test_detects_crashed_process(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)

        mock_proc = MagicMock(spec=subprocess.Popen)
        mock_proc.pid = 300
        mock_proc.poll.return_value = None
        mock_proc.stdout = StringIO("")

        with patch("subprocess.Popen", return_value=mock_proc):
            mgr.start_server("local-ado")

        # Simulate crash
        mock_proc.poll.return_value = 1
        mgr._check_crashes()

        srv = store.get_mcp_server("local-ado")
        assert srv.health == McpServerHealth.STOPPED
        assert srv.pid == 0

    def test_ignores_remote_servers(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        # Should not raise even though remote has no process
        mgr._check_crashes()


# ---------------------------------------------------------------------------
# HTTP probe
# ---------------------------------------------------------------------------

class TestProbeHttp:
    def test_healthy(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = MagicMock()
            healthy, latency = probe_http("http://localhost:3000")
        assert healthy is True
        assert latency >= 0

    def test_server_error(self):
        import urllib.error
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                "http://x", 500, "ISE", {}, None,
            )
            healthy, _ = probe_http("http://localhost:3000")
        assert healthy is False

    def test_client_error_still_healthy(self):
        """4xx means server is up but may need auth — consider healthy."""
        import urllib.error
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.HTTPError(
                "http://x", 401, "Unauthorized", {}, None,
            )
            healthy, _ = probe_http("http://localhost:3000")
        assert healthy is True

    def test_connection_refused(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = ConnectionRefusedError()
            healthy, _ = probe_http("http://localhost:9999")
        assert healthy is False


# ---------------------------------------------------------------------------
# Health loop
# ---------------------------------------------------------------------------

class TestHealthLoop:
    def test_start_stop(self):
        mgr = McpServerManager()
        mgr.start_health_loop(interval=0.1)
        assert mgr._health_thread is not None
        mgr.stop_health_loop()
        assert mgr._health_thread is None

    def test_start_idempotent(self):
        mgr = McpServerManager()
        mgr.start_health_loop(interval=60.0)
        thread1 = mgr._health_thread
        mgr.start_health_loop(interval=60.0)
        assert mgr._health_thread is thread1
        mgr.stop_health_loop()


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestProperties:
    def test_server_names(self, config_file: Path):
        mgr = McpServerManager()
        mgr.load_config(config_file)
        assert sorted(mgr.server_names) == [
            "local-ado", "remote-http", "remote-sse", "stdio-search",
        ]

    def test_is_running_no_process(self):
        mgr = McpServerManager()
        assert mgr.is_running("anything") is False
