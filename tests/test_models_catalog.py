"""Tests for airlock/models_catalog.py"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from airlock.models_catalog import (
    _fetch_gemini_models,
    _get_api_key,
    _load_config,
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
        cfg.write_text("model_list:\n  - model_name: test\n    litellm_params:\n      model: openai/gpt-4o\n")
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
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet", "os.environ/ANTHROPIC_KEY"))
        assert _get_api_key(config, "anthropic") == "sk-ant-test"

    def test_inline_key(self):
        config = _cfg({"model_name": "m", "litellm_params": {"model": "openai/gpt-4o", "api_key": "sk-inline"}})
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

        with patch("airlock.models_catalog.urllib.request.urlopen", return_value=_FakeResp()):
            result = _fetch_gemini_models("fake-key", timeout=5.0)

        assert len(result) == 1
        assert result[0]["id"] == "gemini/gemini-2.5-flash"
        assert result[0]["owned_by"] == "gemini"

    def test_empty_name_skipped(self, monkeypatch):
        payload = json.dumps({"models": [{"name": ""}, {"name": "models/gemini-pro"}]}).encode()

        class _FakeResp:
            def read(self):
                return payload
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass

        with patch("airlock.models_catalog.urllib.request.urlopen", return_value=_FakeResp()):
            result = _fetch_gemini_models("fake-key", timeout=5.0)

        assert len(result) == 1
        assert result[0]["id"] == "gemini/gemini-pro"


# ---------------------------------------------------------------------------
# fetch_live_provider_models
# ---------------------------------------------------------------------------

class TestFetchLiveProviderModels:
    def test_no_keys_returns_empty(self, monkeypatch):
        # Make sure no provider keys are set
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MISTRAL_API_KEY", "GOOGLE_AISTUDIO_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        config = _cfg(_entry("claude", "anthropic/claude-3"))
        result = fetch_live_provider_models(config, timeout=2.0)
        assert result == []

    def test_network_failure_returns_empty(self, monkeypatch):
        """A provider whose endpoint is unreachable should not raise — just return []."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        config = _cfg(_entry("gpt-4o", "openai/gpt-4o", "os.environ/OPENAI_API_KEY"))

        with patch("airlock.models_catalog._fetch_openai_models", side_effect=OSError("refused")):
            result = fetch_live_provider_models(config, timeout=2.0)

        assert isinstance(result, list)  # did not raise
