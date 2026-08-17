"""Tests for airlock/models_catalog.py"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from airlock.models_catalog import (
    _configured_provider_discovery,
    _custom_fetchers_from_config,
    _fetch_gemini_models,
    _get_api_key,
    _load_config,
    _models_url_from_configured_base,
    _provider_fetchers_from_model_list,
    _resolve_secret,
    _safe_discovery_failure,
    _fetch_openai_compatible,
    fetch_live_provider_models,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(*entries: dict) -> dict:
    return {"model_list": list(entries)}


def _entry(alias: str, model: str, api_key: str = "os.environ/FAKE_KEY") -> dict:
    return {"model_name": alias, "litellm_params": {"model": model, "api_key": api_key}}


# ---------------------------------------------------------------------------
# _load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_loads_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "model_list:\n  - model_name: test\n    litellm_params:\n      model: openai/gpt-4o\n"
        )
        result = _load_config(cfg)
        assert result["model_list"][0]["model_name"] == "test"

    def test_missing_file_returns_empty(self, tmp_path):
        result = _load_config(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_invalid_yaml_returns_empty(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(":\n  bad: [unclosed")
        result = _load_config(cfg)
        assert result == {}


# ---------------------------------------------------------------------------
# _get_api_key
# ---------------------------------------------------------------------------


class TestGetApiKey:
    def test_resolves_env_ref(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_KEY", "sk-ant-test")
        config = _cfg(
            _entry(
                "claude-sonnet", "anthropic/claude-sonnet", "os.environ/ANTHROPIC_KEY"
            )
        )
        assert _get_api_key(config, "anthropic") == "sk-ant-test"

    def test_inline_key(self):
        config = _cfg(
            {
                "model_name": "m",
                "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-inline"},
            }
        )
        assert _get_api_key(config, "openai") == "sk-inline"

    def test_missing_env_var_returns_none(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        config = _cfg(_entry("m", "openai/gpt-4o", "os.environ/MISSING_KEY"))
        assert _get_api_key(config, "openai") is None

    def test_wrong_provider_returns_none(self):
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet"))
        assert _get_api_key(config, "openai") is None


# ---------------------------------------------------------------------------
# _fetch_gemini_models — name parsing
# ---------------------------------------------------------------------------


class TestFetchGeminiModels:
    def test_name_field_parsed_to_prefixed_id(self, monkeypatch):
        """'models/gemini-2.5-flash' in the Gemini response → 'gemini/gemini-2.5-flash'."""
        payload = json.dumps({"models": [{"name": "models/gemini-2.5-flash"}]}).encode()

        class _FakeResp:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

        with patch(
            "airlock.models_catalog.urllib.request.urlopen", return_value=_FakeResp()
        ):
            result = _fetch_gemini_models("fake-key", timeout=5.0)

        assert len(result) == 1
        assert result[0]["id"] == "gemini/gemini-2.5-flash"
        assert result[0]["owned_by"] == "gemini"

    def test_empty_name_skipped(self, monkeypatch):
        payload = json.dumps(
            {"models": [{"name": ""}, {"name": "models/gemini-pro"}]}
        ).encode()

        class _FakeResp:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

        with patch(
            "airlock.models_catalog.urllib.request.urlopen", return_value=_FakeResp()
        ):
            result = _fetch_gemini_models("fake-key", timeout=5.0)

        assert len(result) == 1
        assert result[0]["id"] == "gemini/gemini-pro"


# ---------------------------------------------------------------------------
# fetch_live_provider_models
# ---------------------------------------------------------------------------


class TestFetchLiveProviderModels:
    def test_no_keys_returns_empty(self, monkeypatch):
        # Make sure no provider keys are set
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "MISTRAL_API_KEY",
            "GOOGLE_AISTUDIO_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        config = _cfg(_entry("claude", "anthropic/claude-3"))
        result = fetch_live_provider_models(config, timeout=2.0)
        assert result == []

    def test_network_failure_returns_empty(self, monkeypatch):
        """A provider whose endpoint is unreachable should not raise — just return []."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        config = _cfg(_entry("gpt-4o", "openai/gpt-4o", "os.environ/OPENAI_API_KEY"))

        with patch(
            "airlock.models_catalog._fetch_openai_models",
            side_effect=OSError("refused"),
        ):
            result = fetch_live_provider_models(config, timeout=2.0)

        assert isinstance(result, list)  # did not raise


# ---------------------------------------------------------------------------
# Custom providers (issue: support LiteLLM proxy or similar)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class TestResolveSecret:
    def test_literal(self):
        assert _resolve_secret("sk-123") == "sk-123"

    def test_env_ref(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-env")
        assert _resolve_secret("os.environ/MY_KEY") == "sk-env"

    def test_missing_env_ref(self, monkeypatch):
        monkeypatch.delenv("MISSING", raising=False)
        assert _resolve_secret("os.environ/MISSING") is None

    def test_empty_and_non_str(self):
        assert _resolve_secret("") is None
        assert _resolve_secret(None) is None
        assert _resolve_secret(123) is None


class TestConfiguredProviderDiscovery:
    @pytest.mark.parametrize(
        "base,expected",
        [
            ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/models"),
            ("https://api.deepseek.com", "https://api.deepseek.com/models"),
            (
                "https://private.example/v1/chat/completions",
                "https://private.example/v1/models",
            ),
        ],
    )
    def test_models_url_from_reviewed_base(self, base, expected):
        assert _models_url_from_configured_base(base) == expected

    @pytest.mark.parametrize(
        "base",
        [
            None,
            "http://insecure.example/v1",
            "https://user:pass@example/v1",
            "https://example/v1?secret=value",
            "https://example/v1#fragment",
            "https://example/v1/models",
        ],
    )
    def test_unsafe_base_is_rejected_before_key_use(self, base):
        assert _models_url_from_configured_base(base) is None

    def test_requires_one_consistent_base_and_resolvable_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_TEST_KEY", "test-key")
        config = _cfg(
            {
                "model_name": "openrouter/openai/gpt-4o-mini",
                "litellm_params": {
                    "model": "openrouter/openai/gpt-4o-mini",
                    "api_key": "os.environ/OPENROUTER_TEST_KEY",
                    "api_base": "https://openrouter.ai/api/v1",
                },
            }
        )
        assert _configured_provider_discovery(config, "openrouter") == (
            "https://openrouter.ai/api/v1/models",
            "test-key",
        )
        config["model_list"].append(
            {
                "model_name": "openrouter/other",
                "litellm_params": {
                    "model": "openrouter/other",
                    "api_key": "os.environ/OPENROUTER_TEST_KEY",
                    "api_base": "https://other.example/v1",
                },
            }
        )
        assert _configured_provider_discovery(config, "openrouter") is None

    def test_no_configured_base_or_key_means_no_fetcher(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_TEST_KEY", raising=False)
        config = _cfg(
            {
                "model_name": "deepseek/deepseek-chat",
                "litellm_params": {
                    "model": "deepseek/deepseek-chat",
                    "api_key": "os.environ/DEEPSEEK_TEST_KEY",
                    "api_base": "https://api.deepseek.com",
                },
            }
        )
        assert _provider_fetchers_from_model_list(config) == []

    def test_failure_summary_excludes_exception_text_and_keeps_status(self):
        exc = urllib.error.HTTPError(
            "https://example/models", 429, "sentinel-provider-secret", {}, None
        )
        summary = _safe_discovery_failure(exc)
        assert summary == "error_type=HTTPError status=429"
        assert "sentinel" not in summary

    def test_configured_provider_models_are_normalized_without_registration(
        self, monkeypatch
    ):
        monkeypatch.setenv("OPENROUTER_TEST_KEY", "test-key")
        config = _cfg(
            {
                "model_name": "openrouter/openai/gpt-4o-mini",
                "litellm_params": {
                    "model": "openrouter/openai/gpt-4o-mini",
                    "api_key": "os.environ/OPENROUTER_TEST_KEY",
                    "api_base": "https://openrouter.ai/api/v1",
                },
            }
        )
        fetcher = _provider_fetchers_from_model_list(config)[0]

        class _Response:
            def read(self):
                return json.dumps(
                    {"data": [{"id": "openai/gpt-4o-mini", "created": 7}]}
                ).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

        captured: dict[str, object] = {}

        class _Opener:
            def open(self, request, timeout):
                captured["url"] = request.full_url
                captured["authorization"] = request.get_header("Authorization")
                captured["timeout"] = timeout
                return _Response()

        with patch(
            "airlock.models_catalog.urllib.request.build_opener", return_value=_Opener()
        ):
            entries = fetcher.fn(fetcher.api_key_override or "", 2.0)

        assert entries == [
            {
                "id": "openrouter/openai/gpt-4o-mini",
                "object": "model",
                "created": 7,
                "owned_by": "openrouter",
            }
        ]
        assert captured == {
            "url": "https://openrouter.ai/api/v1/models",
            "authorization": "Bearer test-key",
            "timeout": 2.0,
        }

    def test_provider_discovery_uses_no_redirect_opener(self):
        """A redirect is an HTTP error, never a second credential-bearing request."""
        with patch("airlock.models_catalog.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError(
                "https://reviewed.example/models", 302, "redirect", {}, None
            )
            with pytest.raises(urllib.error.HTTPError):
                _fetch_openai_compatible(
                    "https://reviewed.example/models",
                    "deepseek",
                    "sentinel-key",
                    1.0,
                    no_redirect=True,
                )
        handlers = opener.call_args.args
        assert any(handler.__class__.__name__ == "_NoRedirect" for handler in handlers)


class TestCustomFetchersFromConfig:
    def test_builds_fetcher(self, monkeypatch):
        monkeypatch.setenv("LITELLM_KEY", "sk-proxy")
        config = {
            "providers": [
                {
                    "name": "litellm-lan",
                    "base_url": "http://192.168.1.45:4000/v1/models",
                    "api_key": "os.environ/LITELLM_KEY",
                }
            ]
        }
        fetchers = _custom_fetchers_from_config(config)
        assert len(fetchers) == 1
        assert fetchers[0].prefix == "litellm-lan"
        assert fetchers[0].api_key_override == "sk-proxy"

    def test_missing_required_fields_skipped(self):
        config = {"providers": [{"name": "x"}, {"base_url": "http://y"}, {}]}
        assert _custom_fetchers_from_config(config) == []

    def test_missing_key_skipped(self, monkeypatch):
        monkeypatch.delenv("NOPE", raising=False)
        config = {
            "providers": [
                {
                    "name": "p",
                    "base_url": "http://host/v1/models",
                    "api_key": "os.environ/NOPE",
                }
            ]
        }
        assert _custom_fetchers_from_config(config) == []

    @pytest.mark.parametrize("provider", ["openrouter", "deepseek"])
    def test_protected_provider_cannot_use_generic_provider_config(self, provider):
        """A custom entry cannot bypass model-list HTTPS/no-redirect controls."""
        config = {
            "providers": [
                {
                    "name": provider,
                    "base_url": "http://attacker.example/models",
                    "api_key": "sentinel-key",
                }
            ]
        }
        assert _custom_fetchers_from_config(config) == []
        # With no model_list entry, startup has no authorized discovery fetcher.
        assert fetch_live_provider_models(config, timeout=0.1) == []

    def test_fetch_live_includes_custom(self, monkeypatch):
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MISTRAL_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("LITELLM_KEY", "sk-proxy")
        payload = json.dumps(
            {"data": [{"id": "gpt-5.4"}, {"id": "claude-sonnet"}]}
        ).encode()
        config = {
            "providers": [
                {
                    "name": "litellm-lan",
                    "base_url": "http://192.168.1.45:4000/v1/models",
                    "api_key": "os.environ/LITELLM_KEY",
                }
            ]
        }
        with patch(
            "airlock.models_catalog.urllib.request.urlopen",
            return_value=_FakeResp(payload),
        ):
            result = fetch_live_provider_models(config, timeout=2.0)
        ids = sorted(m["id"] for m in result)
        assert ids == ["litellm-lan/claude-sonnet", "litellm-lan/gpt-5.4"]

    def test_custom_prefix_overrides_builtin(self, monkeypatch):
        """A custom 'openai' entry should replace the built-in openai fetcher."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
        monkeypatch.setenv("LITELLM_KEY", "sk-proxy")
        payload = json.dumps({"data": [{"id": "gpt-5.4"}]}).encode()
        config = {
            "model_list": [
                {
                    "model_name": "gpt",
                    "litellm_params": {
                        "model": "openai/gpt-4o",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                }
            ],
            "providers": [
                {
                    "name": "openai",
                    "base_url": "http://proxy/v1/models",
                    "api_key": "os.environ/LITELLM_KEY",
                }
            ],
        }
        with (
            patch(
                "airlock.models_catalog._fetch_openai_models",
                side_effect=AssertionError("built-in must not be called"),
            ),
            patch(
                "airlock.models_catalog.urllib.request.urlopen",
                return_value=_FakeResp(payload),
            ),
        ):
            result = fetch_live_provider_models(config, timeout=2.0)
        assert [m["id"] for m in result] == ["openai/gpt-5.4"]
