"""Tests for airlock/proxy.py"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from airlock.proxy import (
    _find_config,
    _background_health_checks_override,
    _fathom_logger_enabled,
    _mcp_startup_mode,
    _prepare_runtime_config,
    _startup_model_discovery_enabled,
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
        monkeypatch.setenv(
            "AIRLOCK_CONFIG", str(config_file.parent / "nonexistent.yaml")
        )

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
        """subprocess.run should run LiteLLM directly on the public host:port."""
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_HOST", "0.0.0.0")
        monkeypatch.setenv("AIRLOCK_PORT", "4000")

        mock_result = MagicMock(returncode=0)
        with (
            patch("airlock.proxy.subprocess.run", return_value=mock_result) as mock_run,
            patch("airlock.proxy.fetch_live_provider_models", return_value=[]),
            pytest.raises(SystemExit),
        ):
            main()

        cmd = mock_run.call_args[0][0]
        expected_bin = str(Path(sys.executable).parent / "litellm")
        assert cmd[0] == expected_bin
        assert "--host" in cmd
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0"
        assert "--port" in cmd
        assert cmd[cmd.index("--port") + 1] == "4000"

    def test_main_uses_subprocess_run(self, config_file, monkeypatch):
        """Verify subprocess.run is used instead of deprecated subprocess.call."""
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))

        mock_result = MagicMock(returncode=0)
        with (
            patch("airlock.proxy.subprocess.run", return_value=mock_result) as mock_run,
            patch("airlock.proxy.fetch_live_provider_models", return_value=[]),
            pytest.raises(SystemExit),
        ):
            main()

        mock_run.assert_called_once()
        # Verify check=False is passed
        assert mock_run.call_args[1].get("check") is False

    def test_main_default_host_port(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.delenv("AIRLOCK_HOST", raising=False)
        monkeypatch.delenv("AIRLOCK_PORT", raising=False)

        mock_result = MagicMock(returncode=0)
        # Stub out load_dotenv so a developer's local .env doesn't reintroduce
        # AIRLOCK_HOST/AIRLOCK_PORT and shadow the in-code default we're testing.
        with (
            patch("airlock.proxy.load_dotenv", lambda *a, **k: None),
            patch("airlock.proxy.subprocess.run", return_value=mock_result) as mock_run,
            patch("airlock.proxy.fetch_live_provider_models", return_value=[]),
            pytest.raises(SystemExit),
        ):
            main()

        cmd = mock_run.call_args[0][0]
        assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
        assert cmd[cmd.index("--port") + 1] == "4000"

    def test_main_calls_load_dotenv(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        dotenv_called = []

        mock_result = MagicMock(returncode=0)
        with (
            patch(
                "airlock.proxy.load_dotenv",
                side_effect=lambda *a, **kw: dotenv_called.append(True),
            ),
            patch("airlock.proxy.subprocess.run", return_value=mock_result),
            patch("airlock.proxy.fetch_live_provider_models", return_value=[]),
            pytest.raises(SystemExit),
        ):
            main()

        assert len(dotenv_called) == 1

    def test_main_propagates_litellm_returncode(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))

        mock_result = MagicMock(returncode=42)
        with (
            patch("airlock.proxy.subprocess.run", return_value=mock_result),
            patch("airlock.proxy.fetch_live_provider_models", return_value=[]),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 42

    def test_main_calls_live_discovery(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_STARTUP_MODEL_DISCOVERY", "1")
        discovery_called = []

        mock_result = MagicMock(returncode=0)
        with (
            patch(
                "airlock.proxy.fetch_live_provider_models",
                side_effect=lambda *a, **kw: discovery_called.append(True) or [],
            ),
            patch("airlock.proxy.subprocess.run", return_value=mock_result),
            pytest.raises(SystemExit),
        ):
            main()

        assert len(discovery_called) == 1

    def test_main_skips_live_discovery_by_default(self, config_file, monkeypatch):
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.delenv("AIRLOCK_STARTUP_MODEL_DISCOVERY", raising=False)

        mock_result = MagicMock(returncode=0)
        with (
            patch("airlock.proxy.subprocess.run", return_value=mock_result),
            patch("airlock.proxy.fetch_live_provider_models") as mock_discovery,
            pytest.raises(SystemExit),
        ):
            main()

        mock_discovery.assert_not_called()


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


class TestPrepareRuntimeConfigMasterKey:
    def test_strips_master_key_when_env_missing(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "model_list: []\n"
            "general_settings:\n"
            "  master_key: os.environ/AIRLOCK_MASTER_KEY\n"
        )
        monkeypatch.delenv("AIRLOCK_MASTER_KEY", raising=False)

        runtime_path, temp_path = _prepare_runtime_config(str(config_file))

        assert temp_path is not None
        with open(runtime_path, encoding="utf-8") as handle:
            runtime_config = yaml.safe_load(handle) or {}

        assert "master_key" not in (runtime_config.get("general_settings") or {})

    def test_keeps_master_key_when_env_present(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "model_list: []\n"
            "general_settings:\n"
            "  master_key: os.environ/AIRLOCK_MASTER_KEY\n"
        )
        monkeypatch.setenv("AIRLOCK_MASTER_KEY", "sk-airlock-abcdef1234567890")

        runtime_path, _temp_path = _prepare_runtime_config(str(config_file))

        with open(runtime_path, encoding="utf-8") as handle:
            runtime_config = yaml.safe_load(handle) or {}

        assert (runtime_config.get("general_settings") or {}).get("master_key") == (
            "os.environ/AIRLOCK_MASTER_KEY"
        )


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
        monkeypatch.setattr(
            "airlock.callbacks.s3_logger.proxy_s3_logger.flush", mock_flush
        )
        _register_shutdown_handlers()
        import signal

        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(SystemExit) as exc_info:
            handler(signal.SIGTERM, None)
        assert exc_info.value.code == 0
        mock_flush.assert_called_once()

    def test_sigterm_handler_checkpoints_circuit_breaker_state(
        self, monkeypatch, tmp_path
    ):
        """Shutdown handler should checkpoint circuit breaker state."""
        from unittest.mock import MagicMock

        monkeypatch.setattr(
            "airlock.callbacks.s3_logger.proxy_s3_logger.flush", MagicMock()
        )
        monkeypatch.setenv("AIRLOCK_STATE_DIR", str(tmp_path))

        _register_shutdown_handlers()
        import signal

        handler = signal.getsignal(signal.SIGTERM)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)

        # Checkpoint file should exist after shutdown handler ran.
        assert (tmp_path / "cb_state.json").exists()


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
            "model_list:\n  - litellm_params:\n      model: anthropic/claude\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("model_name" in w for w in warnings)

    def test_model_missing_litellm_params_model(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model_list:\n  - model_name: claude\n    litellm_params: {}\n")
        warnings = _validate_config(str(cfg))
        assert any("litellm_params.model" in w for w in warnings)

    def test_guardrail_missing_name(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG + "guardrails:\n"
            "  - litellm_params:\n"
            "      guardrail: airlock.guardrails.pii_guard\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("guardrail_name" in w for w in warnings)

    def test_guardrail_missing_guardrail_param(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG + "guardrails:\n"
            "  - guardrail_name: pii\n"
            "    litellm_params: {}\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("litellm_params.guardrail" in w for w in warnings)

    def test_mcp_stdio_missing_command(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG + "mcp_servers:\n  search:\n    transport: stdio\n"
        )
        warnings = _validate_config(str(cfg))
        assert any("command" in w and "search" in w for w in warnings)

    def test_mcp_http_no_command_required(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG + "mcp_servers:\n"
            "  api:\n"
            "    url: http://localhost:3001/sse\n"
            "    transport: http\n"
        )
        assert _validate_config(str(cfg)) == []

    def test_general_settings_port_wrong_type(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(_VALID_CONFIG + "general_settings:\n  port: not-a-number\n")
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
            "model_list:\n  - litellm_params: {}\nguardrails:\n  - litellm_params: {}\n"
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


class TestStartupFlags:
    def test_startup_model_discovery_default_off(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_STARTUP_MODEL_DISCOVERY", raising=False)
        assert _startup_model_discovery_enabled() is False

    def test_startup_model_discovery_env_on(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_STARTUP_MODEL_DISCOVERY", "true")
        assert _startup_model_discovery_enabled() is True

    def test_mcp_startup_mode_defaults_to_lazy(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_MCP_STARTUP_MODE", raising=False)
        monkeypatch.delenv("AIRLOCK_ENABLE_MCP_SERVERS", raising=False)
        assert _mcp_startup_mode() == "lazy"

    def test_mcp_startup_mode_env_eager(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "eager")
        assert _mcp_startup_mode() == "eager"

    def test_mcp_startup_mode_env_off(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "off")
        assert _mcp_startup_mode() == "off"

    def test_mcp_startup_mode_legacy_disable_maps_to_off(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_MCP_STARTUP_MODE", raising=False)
        monkeypatch.setenv("AIRLOCK_ENABLE_MCP_SERVERS", "0")
        assert _mcp_startup_mode() == "off"

    def test_mcp_startup_mode_legacy_enable_maps_to_eager(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_MCP_STARTUP_MODE", raising=False)
        monkeypatch.setenv("AIRLOCK_ENABLE_MCP_SERVERS", "1")
        assert _mcp_startup_mode() == "eager"

    def test_background_health_checks_override_unset(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", raising=False)
        assert _background_health_checks_override() is None

    def test_background_health_checks_override_false(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", "false")
        assert _background_health_checks_override() is False

    def test_background_health_checks_override_true(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", "true")
        assert _background_health_checks_override() is True

    def test_fathom_logger_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AIRLOCK_ENABLE_FATHOM_LOGGER", raising=False)
        assert _fathom_logger_enabled() is False

    def test_fathom_logger_enabled_by_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ENABLE_FATHOM_LOGGER", "1")
        assert _fathom_logger_enabled() is True


class TestRuntimeConfigPreparation:
    def test_prepare_runtime_config_returns_original_when_no_overrides(
        self, tmp_path, monkeypatch
    ):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(_VALID_CONFIG)

        monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "eager")
        monkeypatch.delenv("AIRLOCK_ENABLE_MCP_SERVERS", raising=False)
        monkeypatch.delenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", raising=False)

        runtime_path, temp_path = _prepare_runtime_config(str(cfg))
        assert runtime_path == str(cfg)
        assert temp_path is None

    def test_prepare_runtime_config_can_strip_mcp_servers_in_off_mode(
        self, tmp_path, monkeypatch
    ):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG
            + "mcp_servers:\n  demo:\n    transport: stdio\n    command: python3\n"
        )
        monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "off")
        monkeypatch.delenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", raising=False)

        runtime_path, temp_path = _prepare_runtime_config(str(cfg))
        assert temp_path is not None

        import yaml

        with open(runtime_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        assert "mcp_servers" not in loaded

    def test_prepare_runtime_config_keeps_mcp_servers_in_lazy_mode(
        self, tmp_path, monkeypatch
    ):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG
            + "mcp_servers:\n  demo:\n    transport: stdio\n    command: python3\n"
        )
        monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "lazy")
        monkeypatch.delenv("AIRLOCK_ENABLE_MCP_SERVERS", raising=False)
        monkeypatch.delenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", raising=False)

        runtime_path, temp_path = _prepare_runtime_config(str(cfg))
        assert runtime_path == str(cfg)
        assert temp_path is None

    def test_prepare_runtime_config_can_override_health_checks(
        self, tmp_path, monkeypatch
    ):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG
            + "general_settings:\n  background_health_checks: true\n"
        )
        monkeypatch.setenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", "0")
        monkeypatch.delenv("AIRLOCK_ENABLE_MCP_SERVERS", raising=False)

        runtime_path, temp_path = _prepare_runtime_config(str(cfg))
        assert temp_path is not None

        import yaml

        with open(runtime_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        assert loaded["general_settings"]["background_health_checks"] is False

    def test_prepare_runtime_config_can_enable_fathom_logger(
        self, tmp_path, monkeypatch
    ):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            _VALID_CONFIG
            + "litellm_settings:\n"
            + "  success_callback: [\"airlock.callbacks.enterprise_logger.proxy_logger\"]\n"
            + "  failure_callback: [\"airlock.callbacks.enterprise_logger.proxy_logger\"]\n"
        )
        monkeypatch.setenv("AIRLOCK_ENABLE_FATHOM_LOGGER", "1")
        monkeypatch.setenv("AIRLOCK_MCP_STARTUP_MODE", "eager")
        monkeypatch.delenv("AIRLOCK_BACKGROUND_HEALTH_CHECKS", raising=False)

        runtime_path, temp_path = _prepare_runtime_config(str(cfg))
        assert temp_path is not None

        import yaml

        with open(runtime_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}

        fathom = "airlock.callbacks.fathom_logger.proxy_fathom_logger"
        assert fathom in loaded["litellm_settings"]["success_callback"]
        assert fathom in loaded["litellm_settings"]["failure_callback"]

    def test_shadow_mode_no_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")
        _warn_observe_mode()
        captured = capsys.readouterr()
        assert "observe" not in captured.err.lower()


# ---------------------------------------------------------------------------
# Circuit breaker health endpoint
# ---------------------------------------------------------------------------
class TestCircuitHealthEndpoint:
    def test_get_circuit_health_empty(self):
        from airlock.fast.state import StateStore
        from airlock.health import get_circuit_health

        store = StateStore()
        result = get_circuit_health(store)
        assert result["status"] == "ok"
        assert result["circuits"] == {}

    def test_get_circuit_health_with_models(self):
        from airlock.fast.state import CircuitState, StateStore
        from airlock.health import get_circuit_health

        store = StateStore()
        store.get_model("gpt-4o")
        model_b = store.get_model("claude-sonnet")
        # model_a stays closed (default)
        # model_b is open
        model_b.circuit = CircuitState.OPEN
        model_b.consecutive_failures = 7

        result = get_circuit_health(store)
        assert result["status"] == "degraded"
        assert "gpt-4o" in result["circuits"]
        assert result["circuits"]["gpt-4o"]["state"] == "closed"
        assert "claude-sonnet" in result["circuits"]
        assert result["circuits"]["claude-sonnet"]["state"] == "open"
        assert result["circuits"]["claude-sonnet"]["consecutive_failures"] == 7

    def test_get_circuit_health_half_open(self):
        from airlock.fast.state import CircuitState, StateStore
        from airlock.health import get_circuit_health

        store = StateStore()
        model = store.get_model("gpt-4o")
        model.circuit = CircuitState.HALF_OPEN

        result = get_circuit_health(store)
        assert result["status"] == "degraded"
        assert result["circuits"]["gpt-4o"]["state"] == "half_open"

    def test_install_circuit_health_endpoint(self):
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from airlock.fast.state import CircuitState, StateStore
        from airlock.health import install_circuit_health_endpoint

        app = fastapi.FastAPI()
        store = StateStore()
        model = store.get_model("gpt-4o")
        model.circuit = CircuitState.OPEN
        model.consecutive_failures = 5

        install_circuit_health_endpoint(app, store)
        client = TestClient(app)
        resp = client.get("/health/circuits")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["circuits"]["gpt-4o"]["state"] == "open"


class TestConfigValidationExtra:
    def test_shipped_template_is_valid(self):
        template = (
            Path(__file__).resolve().parent.parent
            / "airlock"
            / "cli"
            / "templates"
            / "config.yaml"
        )
        if template.exists():
            warnings = _validate_config(str(template))
            assert warnings == [], f"Template config has warnings: {warnings}"
