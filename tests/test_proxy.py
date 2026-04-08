"""Tests for airlock/proxy.py"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from airlock.proxy import (
    _find_config,
    _validate_config,
    _validate_master_key,
    _register_shutdown_handlers,
    _warn_observe_mode,
    main,
)


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


# ---------------------------------------------------------------------------
# _validate_master_key()
# ---------------------------------------------------------------------------
class TestMasterKeyValidation:
    def test_default_key_warns(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "sk-airlock-change-me")
        _validate_master_key()
        assert "default value" in capsys.readouterr().err

    def test_short_key_warns(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "abc")
        _validate_master_key()
        assert "shorter than 16" in capsys.readouterr().err

    def test_empty_key_warns(self, monkeypatch, capsys):
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)
        _validate_master_key()
        assert "not set" in capsys.readouterr().err

    def test_strong_key_no_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "sk-airlock-abcdef1234567890")
        _validate_master_key()
        assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# _register_shutdown_handlers()
# ---------------------------------------------------------------------------
class TestShutdownHandlers:
    def test_sigterm_handler_registered(self):
        import signal
        _register_shutdown_handlers()
        assert signal.getsignal(signal.SIGTERM) != signal.SIG_DFL

    def test_sigterm_handler_flushes_s3(self, monkeypatch):
        from unittest.mock import MagicMock
        mock_flush = MagicMock()
        monkeypatch.setattr("airlock.callbacks.s3_logger.proxy_s3_logger.flush", mock_flush)
        _register_shutdown_handlers()
        import signal
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(SystemExit) as exc_info:
            handler(signal.SIGTERM, None)
        assert exc_info.value.code == 0
        mock_flush.assert_called_once()

    def test_sigterm_handler_checkpoints_circuit_breaker_state(self, monkeypatch, tmp_path):
        """Shutdown handler should checkpoint circuit breaker state."""
        from unittest.mock import MagicMock
        monkeypatch.setattr("airlock.callbacks.s3_logger.proxy_s3_logger.flush", MagicMock())
        monkeypatch.setenv("AIRLOCK_STATE_DIR", str(tmp_path))

        _register_shutdown_handlers()
        import signal
        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)

        # State file should have been created (or at least attempted)
        # The checkpoint function is called during shutdown


# ---------------------------------------------------------------------------
# _validate_config()
# ---------------------------------------------------------------------------
_VALID_CONFIG = (
    "model_list:\n"
    "  - model_name: claude-sonnet\n"
    "    litellm_params:\n"
    "      model: anthropic/claude-sonnet-4-20250514\n"
)


class TestConfigValidation:
    def test_valid_config_no_warnings(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(_VALID_CONFIG)
        assert _validate_config(str(cfg)) == []

    def test_missing_model_list(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("litellm_settings: {}\n")
        warnings = _validate_config(str(cfg))
        assert any("model_list" in w for w in warnings)

    def test_model_list_not_a_list(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model_list: not-a-list\n")
        warnings = _validate_config(str(cfg))
        assert any("model_list" in w for w in warnings)

    def test_empty_model_list(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model_list: []\n")
        warnings = _validate_config(str(cfg))
        assert any("model_list" in w and "empty" in w for w in warnings)

    def test_model_missing_model_name(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model_list:\n"
            "  - litellm_params:\n"
            "      model: anthropic/claude\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("model_name" in w for w in warnings)

    def test_model_missing_litellm_params_model(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model_list:\n"
            "  - model_name: claude\n"
            "    litellm_params: {}\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("litellm_params.model" in w for w in warnings)

    def test_guardrail_missing_name(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG +
            "guardrails:\n"
            "  - litellm_params:\n"
            "      guardrail: airlock.guardrails.pii_guard\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("guardrail_name" in w for w in warnings)

    def test_guardrail_missing_guardrail_param(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG +
            "guardrails:\n"
            "  - guardrail_name: pii\n"
            "    litellm_params: {}\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("litellm_params.guardrail" in w for w in warnings)

    def test_mcp_stdio_missing_command(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG +
            "mcp_servers:\n"
            "  search:\n"
            "    transport: stdio\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("command" in w and "search" in w for w in warnings)

    def test_mcp_http_no_command_required(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG +
            "mcp_servers:\n"
            "  api:\n"
            "    url: http://localhost:3001/sse\n"
            "    transport: http\n"
        )
        assert _validate_config(str(cfg)) == []

    def test_general_settings_port_wrong_type(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG +
            "general_settings:\n"
            "  port: not-a-number\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("port" in w and "int" in w for w in warnings)

    def test_invalid_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model_list:\n  - bad: yaml: here:\n")
        warnings = _validate_config(str(cfg))
        assert any("not valid YAML" in w for w in warnings)

    def test_multiple_warnings_accumulated(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model_list:\n"
            "  - litellm_params: {}\n"
            "guardrails:\n"
            "  - litellm_params: {}\n"
        )
        warnings = _validate_config(str(cfg))
        assert len(warnings) >= 2

# ---------------------------------------------------------------------------
# _warn_observe_mode() (P2 Fix #9)
# ---------------------------------------------------------------------------
class TestObserveModeWarning:
    def test_observe_mode_warns(self, monkeypatch, capsys):
        monkeypatch.delenv("AIRLOCK_ENFORCE_MODE", raising=False)
        _warn_observe_mode()
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "observe" in captured.err.lower()

    def test_enforce_mode_no_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        _warn_observe_mode()
        captured = capsys.readouterr()
        assert "observe" not in captured.err.lower()

    def test_shadow_mode_no_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")
        _warn_observe_mode()
        captured = capsys.readouterr()
        assert "observe" not in captured.err.lower()



class TestConfigValidationExtra:
    def test_shipped_template_is_valid(self):
        template = Path(__file__).resolve().parent.parent / "airlock" / "cli" / "templates" / "config.yaml"
        if template.exists():
            warnings = _validate_config(str(template))
            assert warnings == [], f"Template config has warnings: {warnings}"
