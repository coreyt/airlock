"""Tests for airlock/proxy.py"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from airlock.proxy import _find_config, main


# ---------------------------------------------------------------------------
# _find_config()
# ---------------------------------------------------------------------------
class TestFindConfig:
    def test_env_var_config(self, tmp_path, monkeypatch):
        config = tmp_path / "custom.yaml"
        config.write_text("model_list: []")
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config))
        assert _find_config() == str(config)

    def test_project_root_config(self, config_file, monkeypatch):
        # Set AIRLOCK_CONFIG to a non-existent path so first candidate fails,
        # then patch __file__ so parent.parent points to tmp_path
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file.parent / "nonexistent.yaml"))

        import airlock.proxy as proxy_mod

        monkeypatch.setattr(
            proxy_mod,
            "__file__",
            str(config_file.parent / "airlock" / "proxy.py"),
        )
        result = _find_config()
        assert result == str(config_file)

    def test_missing_config_exits(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(tmp_path / "nonexistent.yaml"))
        # Also make sure project root doesn't have one
        import airlock.proxy as proxy_mod

        monkeypatch.setattr(
            proxy_mod, "__file__", str(tmp_path / "airlock" / "proxy.py")
        )
        with pytest.raises(SystemExit) as exc_info:
            _find_config()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# main() — helpers
# ---------------------------------------------------------------------------

def _fake_proc(returncode: int = 0):
    """Return a mock subprocess.Popen-like object."""
    proc = MagicMock()
    proc.poll.return_value = returncode  # already exited
    proc.returncode = returncode
    proc.wait.return_value = None
    proc.terminate.return_value = None
    proc.kill.return_value = None
    return proc


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------
class TestMain:
    def test_main_starts_litellm_on_internal_port(self, config_file, monkeypatch):
        """LiteLLM should be Popen'd on 127.0.0.1 at AIRLOCK_PORT+1."""
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_HOST", "0.0.0.0")
        monkeypatch.setenv("AIRLOCK_PORT", "5000")
        monkeypatch.delenv("AIRLOCK_INTERNAL_PORT", raising=False)

        captured = {}
        proc = _fake_proc()

        with patch("airlock.proxy.subprocess.Popen", return_value=proc) as mock_popen, \
             patch("airlock.proxy.uvicorn.run"), \
             patch("airlock.sidecar.make_app", return_value=MagicMock()), \
             pytest.raises(SystemExit):
            main()

        cmd = mock_popen.call_args[0][0]
        expected_bin = str(Path(sys.executable).parent / "litellm")
        assert cmd[0] == expected_bin
        assert "--host" in cmd
        assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
        assert "--port" in cmd
        assert cmd[cmd.index("--port") + 1] == "5001"  # port+1

    def test_main_starts_uvicorn_on_public_port(self, config_file, monkeypatch):
        """uvicorn.run should be called with the public host and port."""
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_HOST", "0.0.0.0")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")

        proc = _fake_proc()
        uvicorn_calls = []

        with patch("airlock.proxy.subprocess.Popen", return_value=proc), \
             patch("airlock.proxy.uvicorn.run", side_effect=lambda app, **kw: uvicorn_calls.append(kw)), \
             patch("airlock.sidecar.make_app", return_value=MagicMock()), \
             pytest.raises(SystemExit):
            main()

        assert len(uvicorn_calls) == 1
        assert uvicorn_calls[0]["host"] == "0.0.0.0"
        assert uvicorn_calls[0]["port"] == 4000

    def test_main_internal_port_env_override(self, config_file, monkeypatch):
        """AIRLOCK_INTERNAL_PORT overrides the default port+1."""
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_PORT", "4000")
        monkeypatch.setenv("AIRLOCK_INTERNAL_PORT", "9999")

        proc = _fake_proc()

        with patch("airlock.proxy.subprocess.Popen", return_value=proc) as mock_popen, \
             patch("airlock.proxy.uvicorn.run"), \
             patch("airlock.sidecar.make_app", return_value=MagicMock()), \
             pytest.raises(SystemExit):
            main()

        cmd = mock_popen.call_args[0][0]
        assert cmd[cmd.index("--port") + 1] == "9999"

    def test_main_calls_load_dotenv(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        dotenv_called = []

        with patch("airlock.proxy.load_dotenv", side_effect=lambda *a, **kw: dotenv_called.append(True)), \
             patch("airlock.proxy.subprocess.Popen", return_value=_fake_proc()), \
             patch("airlock.proxy.uvicorn.run"), \
             patch("airlock.sidecar.make_app", return_value=MagicMock()), \
             pytest.raises(SystemExit):
            main()

        assert len(dotenv_called) == 1

    def test_main_terminates_litellm_on_uvicorn_exit(self, config_file, monkeypatch):
        """When uvicorn exits, LiteLLM subprocess should be terminated."""
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))

        # Proc still running when checked in the finally block
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        proc.returncode = 0
        proc.wait.return_value = None

        with patch("airlock.proxy.subprocess.Popen", return_value=proc), \
             patch("airlock.proxy.uvicorn.run"), \
             patch("airlock.sidecar.make_app", return_value=MagicMock()), \
             pytest.raises(SystemExit):
            main()

        proc.terminate.assert_called_once()

    def test_main_default_host_port(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.delenv("AIRLOCK_HOST", raising=False)
        monkeypatch.delenv("AIRLOCK_PORT", raising=False)

        proc = _fake_proc()
        uvicorn_calls = []

        with patch("airlock.proxy.subprocess.Popen", return_value=proc), \
             patch("airlock.proxy.uvicorn.run", side_effect=lambda app, **kw: uvicorn_calls.append(kw)), \
             patch("airlock.sidecar.make_app", return_value=MagicMock()), \
             pytest.raises(SystemExit):
            main()

        assert uvicorn_calls[0]["host"] == "0.0.0.0"
        assert uvicorn_calls[0]["port"] == 4000
