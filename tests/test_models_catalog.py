"""Tests for airlock/models_catalog.py"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from airlock.models_catalog import (
    _STATIC_CREATED,
    _fetch_gemini_models,
    _get_api_key,
    _load_config,
    _owned_by,
    build_catalog_from_config,
    build_full_catalog,
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
# _owned_by
# ---------------------------------------------------------------------------

class TestOwnedBy:
    def test_provider_prefix_extracted(self):
        assert _owned_by("anthropic/claude-sonnet-4-20250514") == "anthropic"

    def test_bare_name_returns_airlock(self):
        assert _owned_by("claude-sonnet") == "airlock"

    def test_gemini_prefix(self):
        assert _owned_by("gemini/gemini-2.5-flash") == "gemini"

    def test_openai_prefix(self):
        assert _owned_by("openai/gpt-4o") == "openai"


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
# build_catalog_from_config
# ---------------------------------------------------------------------------

class TestBuildCatalogFromConfig:
    def test_alias_name_included(self):
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet-4-20250514"))
        models = build_catalog_from_config(config=config)
        ids = [m["id"] for m in models]
        assert "claude-sonnet" in ids

    def test_provider_pinned_included(self):
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet-4-20250514"))
        models = build_catalog_from_config(config=config)
        ids = [m["id"] for m in models]
        assert "anthropic/claude-sonnet-4-20250514" in ids

    def test_alias_owned_by_airlock(self):
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet-4-20250514"))
        models = build_catalog_from_config(config=config)
        alias = next(m for m in models if m["id"] == "claude-sonnet")
        assert alias["owned_by"] == "airlock"

    def test_pinned_owned_by_provider(self):
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet-4-20250514"))
        models = build_catalog_from_config(config=config)
        pinned = next(m for m in models if m["id"] == "anthropic/claude-sonnet-4-20250514")
        assert pinned["owned_by"] == "anthropic"

    def test_no_duplicates_when_alias_equals_model(self):
        # model_name IS the provider-pinned id
        config = _cfg({"model_name": "openai/gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}})
        models = build_catalog_from_config(config=config)
        assert len([m for m in models if m["id"] == "openai/gpt-4o"]) == 1

    def test_required_fields_present(self):
        config = _cfg(_entry("gpt-4o", "openai/gpt-4o"))
        models = build_catalog_from_config(config=config)
        for m in models:
            assert "id" in m
            assert m["object"] == "model"
            assert isinstance(m["created"], int)
            assert "owned_by" in m

    def test_created_is_static(self):
        config = _cfg(_entry("gpt-4o", "openai/gpt-4o"))
        models = build_catalog_from_config(config=config)
        for m in models:
            assert m["created"] == _STATIC_CREATED

    def test_empty_model_list(self):
        assert build_catalog_from_config(config={}) == []

    def test_multiple_providers(self):
        config = _cfg(
            _entry("claude-sonnet", "anthropic/claude-sonnet-4-20250514"),
            _entry("gpt-4o", "openai/gpt-4o"),
            _entry("gemini-flash", "gemini/gemini-2.5-flash"),
        )
        models = build_catalog_from_config(config=config)
        ids = [m["id"] for m in models]
        assert "claude-sonnet" in ids
        assert "anthropic/claude-sonnet-4-20250514" in ids
        assert "gpt-4o" in ids
        assert "openai/gpt-4o" in ids
        assert "gemini-flash" in ids
        assert "gemini/gemini-2.5-flash" in ids

    def test_no_duplicate_ids_across_entries(self):
        config = _cfg(
            _entry("m", "openai/gpt-4o"),
            _entry("m2", "openai/gpt-4o"),  # same provider model, different alias
        )
        models = build_catalog_from_config(config=config)
        ids = [m["id"] for m in models]
        assert ids.count("openai/gpt-4o") == 1

    def test_missing_config_file_returns_empty(self, tmp_path):
        models = build_catalog_from_config(config_path=tmp_path / "no.yaml")
        assert models == []


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
# fetch_live_provider_models — stubbed HTTP server
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


# ---------------------------------------------------------------------------
# build_full_catalog
# ---------------------------------------------------------------------------

class TestBuildFullCatalog:
    def test_no_live_fetch(self):
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet-4-20250514"))
        models = build_full_catalog(config=config, fetch_live=False)
        ids = [m["id"] for m in models]
        assert "claude-sonnet" in ids
        assert "anthropic/claude-sonnet-4-20250514" in ids

    def test_live_models_added_without_duplicates(self, monkeypatch):
        config = _cfg(_entry("claude-sonnet", "anthropic/claude-sonnet-4-20250514"))

        live_extra = [{"id": "anthropic/claude-3-haiku-20240307", "object": "model", "created": 1704067200, "owned_by": "anthropic"}]
        live_dup = [{"id": "anthropic/claude-sonnet-4-20250514", "object": "model", "created": 1704067200, "owned_by": "anthropic"}]

        with patch("airlock.models_catalog.fetch_live_provider_models", return_value=live_extra + live_dup):
            models = build_full_catalog(config=config, fetch_live=True)

        ids = [m["id"] for m in models]
        # Config entries are there
        assert "claude-sonnet" in ids
        assert "anthropic/claude-sonnet-4-20250514" in ids
        # Live extra is added
        assert "anthropic/claude-3-haiku-20240307" in ids
        # No duplicates
        assert ids.count("anthropic/claude-sonnet-4-20250514") == 1

    def test_empty_config_no_live_fetch(self):
        models = build_full_catalog(config={}, fetch_live=False)
        assert models == []
