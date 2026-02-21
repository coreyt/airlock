"""Tests for airlock/proxy.py"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    def test_main_builds_correct_command(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_HOST", "127.0.0.1")
        monkeypatch.setenv("AIRLOCK_PORT", "8080")

        captured_cmd = []

        def fake_subprocess_call(cmd):
            captured_cmd.extend(cmd)
            return 0

        with patch("airlock.proxy.subprocess.call", side_effect=fake_subprocess_call):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        expected_bin = str(Path(sys.executable).parent / "litellm")
        assert captured_cmd[0] == expected_bin
        assert "--config" in captured_cmd
        assert str(config_file) in captured_cmd
        assert "--host" in captured_cmd
        assert "127.0.0.1" in captured_cmd
        assert "--port" in captured_cmd
        assert "8080" in captured_cmd

    def test_main_calls_load_dotenv(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        dotenv_called = []

        with patch("airlock.proxy.load_dotenv", side_effect=lambda: dotenv_called.append(True)):
            with patch("airlock.proxy.subprocess.call", return_value=0):
                with pytest.raises(SystemExit):
                    main()

        assert len(dotenv_called) == 1

    def test_main_default_host_port(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        captured_cmd = []

        def fake_subprocess_call(cmd):
            captured_cmd.extend(cmd)
            return 0

        with patch("airlock.proxy.subprocess.call", side_effect=fake_subprocess_call):
            with pytest.raises(SystemExit):
                main()

        host_idx = captured_cmd.index("--host")
        port_idx = captured_cmd.index("--port")
        assert captured_cmd[host_idx + 1] == "0.0.0.0"
        assert captured_cmd[port_idx + 1] == "4000"
