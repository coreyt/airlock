"""Tests for airlock.tui.proxy_manager — proxy subprocess management."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from airlock.tui.proxy_manager import ProxyManager


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


def test_stop_terminates_process() -> None:
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None  # alive
    fake_proc.wait.return_value = 0
    pm._process = fake_proc

    pm.stop()

    fake_proc.terminate.assert_called_once()
    assert pm._process is None


def test_stop_kills_on_timeout() -> None:
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.poll.return_value = None  # alive
    fake_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=5), 0]
    pm._process = fake_proc

    pm.stop()

    fake_proc.terminate.assert_called_once()
    fake_proc.kill.assert_called_once()
    assert pm._process is None


def test_stop_noop_when_not_running() -> None:
    pm = ProxyManager()
    pm.stop()  # should not raise
    assert pm._process is None


def test_stop_noop_when_already_exited() -> None:
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


def test_stdout_stream_returns_pipe() -> None:
    pm = ProxyManager()
    fake_proc = mock.MagicMock(spec=subprocess.Popen)
    fake_proc.stdout = mock.sentinel.pipe
    pm._process = fake_proc

    assert pm.stdout_stream is mock.sentinel.pipe


def test_stdout_stream_returns_none_when_no_process() -> None:
    pm = ProxyManager()
    assert pm.stdout_stream is None
