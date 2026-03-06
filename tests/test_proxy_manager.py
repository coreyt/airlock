"""Tests for airlock.tui.proxy_manager — proxy subprocess management."""

from __future__ import annotations

import io
import subprocess
import threading
from pathlib import Path
from unittest import mock

import pytest

from airlock.tui.proxy_manager import ProxyManager, _MAX_LOG_LINES


# -------------------------------------------------------------------
# find_config
# -------------------------------------------------------------------


def test_find_config_returns_path_when_exists(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []\n")
    with mock.patch.dict("os.environ", {"AIRLOCK_CONFIG": str(cfg)}):
        pm = ProxyManager()
        assert pm.find_config() == cfg


def test_find_config_returns_none_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIRLOCK_CONFIG", str(tmp_path / "nope.yaml"))
    # Also patch the module-relative candidate to avoid finding the real config
    fake_module = tmp_path / "fake" / "tui" / "proxy_manager.py"
    fake_module.parent.mkdir(parents=True)
    monkeypatch.setattr("airlock.tui.proxy_manager.__file__", str(fake_module))
    pm = ProxyManager()
    assert pm.find_config() is None


# -------------------------------------------------------------------
# preflight
# -------------------------------------------------------------------


def test_preflight_passes_when_config_exists(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []\n")
    pm = ProxyManager()
    pm.find_config = mock.Mock(return_value=cfg)
    assert pm.preflight() is None


def test_preflight_fails_when_config_missing() -> None:
    pm = ProxyManager()
    pm.find_config = mock.Mock(return_value=None)
    err = pm.preflight()
    assert err is not None
    assert "config.yaml" in err


# -------------------------------------------------------------------
# start
# -------------------------------------------------------------------


def test_start_launches_popen(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []\n")
    pm = ProxyManager(host="127.0.0.1", port="9999")
    pm.find_config = mock.Mock(return_value=cfg)

    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None
    fake_proc.stdout = None

    with mock.patch("airlock.tui.proxy_manager.subprocess.Popen", return_value=fake_proc) as mock_popen:
        err = pm.start()

    assert err is None
    mock_popen.assert_called_once()
    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    assert cmd[0].endswith("/litellm") or cmd[0].endswith("\\litellm")
    assert "--host" in cmd
    assert "127.0.0.1" in cmd
    assert "--port" in cmd
    assert "9999" in cmd


def test_start_rejects_double_start(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []\n")
    pm = ProxyManager()
    pm.find_config = mock.Mock(return_value=cfg)

    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None  # still alive
    fake_proc.stdout = None

    with mock.patch("airlock.tui.proxy_manager.subprocess.Popen", return_value=fake_proc):
        pm.start()

    err = pm.start()
    assert err is not None
    assert "already running" in err.lower()


def test_start_returns_error_when_preflight_fails() -> None:
    pm = ProxyManager()
    pm.find_config = mock.Mock(return_value=None)
    err = pm.start()
    assert err is not None
    assert "config.yaml" in err


# -------------------------------------------------------------------
# stop
# -------------------------------------------------------------------


def test_stop_terminates_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None  # alive
    fake_proc.wait.return_value = 0
    pm._process = fake_proc

    pm.stop()

    fake_proc.terminate.assert_called_once()
    assert pm._process is None


def test_stop_kills_on_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None  # alive
    fake_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=5), 0]
    pm._process = fake_proc

    pm.stop()

    fake_proc.terminate.assert_called_once()
    fake_proc.kill.assert_called_once()
    assert pm._process is None


def test_stop_noop_when_not_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()
    pm.stop()  # should not raise
    assert pm._process is None


def test_stop_noop_when_already_exited(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = 0  # already exited
    pm._process = fake_proc

    pm.stop()
    fake_proc.terminate.assert_not_called()
    assert pm._process is None


# -------------------------------------------------------------------
# properties
# -------------------------------------------------------------------


def test_is_tui_owned_true_when_alive() -> None:
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None
    pm._process = fake_proc

    assert pm.is_tui_owned is True


def test_is_tui_owned_false_when_no_process() -> None:
    pm = ProxyManager()
    assert pm.is_tui_owned is False


def test_is_tui_owned_false_when_exited() -> None:
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = 1
    pm._process = fake_proc

    assert pm.is_tui_owned is False


def test_find_config_static_returns_path_or_none() -> None:
    result = ProxyManager.find_config_static()
    # Returns a Path if config.yaml exists, None otherwise
    assert result is None or hasattr(result, "is_file")


def test_output_queue_exists() -> None:
    pm = ProxyManager()
    assert pm.output_queue is not None


# -------------------------------------------------------------------
# ring-buffer console log
# -------------------------------------------------------------------


def test_log_path_uses_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()
    assert pm.log_path == tmp_path / "proxy-console.log"


def test_log_path_defaults_to_logs(monkeypatch) -> None:
    monkeypatch.delenv("AIRLOCK_LOG_DIR", raising=False)
    pm = ProxyManager()
    assert pm.log_path == Path("./logs/proxy-console.log")


def test_reader_thread_tees_to_queue_and_ring(tmp_path: Path, monkeypatch) -> None:
    """Verify the reader thread writes lines to both queue and ring."""
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()

    # Simulate subprocess stdout with a StringIO
    lines = ["line one\n", "line two\n", "line three\n"]
    fake_stdout = io.StringIO("".join(lines))

    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.stdout = fake_stdout
    fake_proc.poll.return_value = None
    pm._process = fake_proc

    # Run reader loop directly (not in a thread, for determinism)
    pm._reader_loop()

    # Check ring buffer
    assert list(pm._ring) == ["line one", "line two", "line three"]

    # Check output queue
    queued = []
    while not pm._output_queue.empty():
        queued.append(pm._output_queue.get_nowait())
    assert queued == ["line one", "line two", "line three"]


def test_stop_flushes_ring_to_file(tmp_path: Path, monkeypatch) -> None:
    """Verify stop() persists ring buffer to disk."""
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()

    # Populate ring directly
    pm._ring.extend(["alpha", "beta", "gamma"])

    # No process running — stop should still flush
    pm.stop()

    log_file = tmp_path / "proxy-console.log"
    assert log_file.exists()
    content = log_file.read_text()
    assert content == "alpha\nbeta\ngamma\n"


def test_load_ring_reads_existing_file(tmp_path: Path, monkeypatch) -> None:
    """Verify _load_ring picks up lines from a previous session."""
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    log_file = tmp_path / "proxy-console.log"
    log_file.write_text("old line 1\nold line 2\n")

    pm = ProxyManager()
    pm._load_ring()

    assert list(pm._ring) == ["old line 1", "old line 2"]


def test_ring_respects_max_lines(tmp_path: Path, monkeypatch) -> None:
    """Ring buffer drops oldest lines when exceeding _MAX_LOG_LINES."""
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    pm = ProxyManager()

    # Fill beyond capacity
    for i in range(_MAX_LOG_LINES + 500):
        pm._ring.append(f"line {i}")

    assert len(pm._ring) == _MAX_LOG_LINES
    # Oldest lines (0-499) should be gone, newest should be present
    assert pm._ring[0] == "line 500"
    assert pm._ring[-1] == f"line {_MAX_LOG_LINES + 499}"


def test_ring_truncates_on_load(tmp_path: Path, monkeypatch) -> None:
    """Loading a file with >1000 lines keeps only the last 1000."""
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    log_file = tmp_path / "proxy-console.log"

    # Write 1500 lines to existing log
    with open(log_file, "w") as f:
        for i in range(1500):
            f.write(f"line {i}\n")

    pm = ProxyManager()
    pm._load_ring()

    assert len(pm._ring) == _MAX_LOG_LINES
    assert pm._ring[0] == "line 500"
    assert pm._ring[-1] == "line 1499"


def test_flush_creates_parent_dir(tmp_path: Path, monkeypatch) -> None:
    """flush_ring creates the log directory if it doesn't exist."""
    log_dir = tmp_path / "nested" / "logs"
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_dir))

    pm = ProxyManager()
    pm._ring.append("test line")
    pm._flush_ring()

    assert (log_dir / "proxy-console.log").exists()


def test_start_loads_existing_log(tmp_path: Path, monkeypatch) -> None:
    """start() picks up lines from a previous session's log file."""
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
    log_file = tmp_path / "proxy-console.log"
    log_file.write_text("previous session line\n")

    cfg = tmp_path / "config.yaml"
    cfg.write_text("model_list: []\n")
    pm = ProxyManager()
    pm.find_config = mock.Mock(return_value=cfg)

    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None
    fake_proc.stdout = None
    with mock.patch("airlock.tui.proxy_manager.subprocess.Popen", return_value=fake_proc):
        pm.start()

    assert "previous session line" in pm._ring
