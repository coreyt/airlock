"""Tests for airlock/proxy.py"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

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
# main()
# ---------------------------------------------------------------------------
class TestMain:
    def test_main_starts_litellm_on_public_port(self, config_file, monkeypatch):
        """subprocess.call should run LiteLLM directly on the public host:port."""
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_HOST", "0.0.0.0")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")

        with patch("airlock.proxy.subprocess.call", return_value=0) as mock_call, \
             patch("airlock.proxy.fetch_live_provider_models", return_value=[]), \
             pytest.raises(SystemExit):
            main()

        cmd = mock_call.call_args[0][0]
        expected_bin = str(Path(sys.executable).parent / "litellm")
        assert cmd[0] == expected_bin
        assert "--host" in cmd
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0"
        assert "--port" in cmd
        assert cmd[cmd.index("--port") + 1] == "4000"

    def test_main_default_host_port(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.delenv("AIRLOCK_HOST", raising=False)
        monkeypatch.delenv("AIRLOCK_PORT", raising=False)

        with patch("airlock.proxy.subprocess.call", return_value=0) as mock_call, \
             patch("airlock.proxy.fetch_live_provider_models", return_value=[]), \
             pytest.raises(SystemExit):
            main()

        cmd = mock_call.call_args[0][0]
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0"
        assert cmd[cmd.index("--port") + 1] == "4000"

    def test_main_calls_load_dotenv(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        dotenv_called = []

        with patch("airlock.proxy.load_dotenv", side_effect=lambda *a, **kw: dotenv_called.append(True)), \
             patch("airlock.proxy.subprocess.call", return_value=0), \
             patch("airlock.proxy.fetch_live_provider_models", return_value=[]), \
             pytest.raises(SystemExit):
            main()

        assert len(dotenv_called) == 1

    def test_main_propagates_litellm_returncode(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))

        with patch("airlock.proxy.subprocess.call", return_value=42), \
             patch("airlock.proxy.fetch_live_provider_models", return_value=[]), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 42

    def test_main_calls_live_discovery(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        discovery_called = []

        with patch("airlock.proxy.fetch_live_provider_models",
                   side_effect=lambda *a, **kw: discovery_called.append(True) or []), \
             patch("airlock.proxy.subprocess.call", return_value=0), \
             pytest.raises(SystemExit):
            main()

        assert len(discovery_called) == 1
