"""Tests for airlock.advisor.model_select — advisor model selection."""

from __future__ import annotations

import pytest

from airlock.advisor.model_select import select_advisor_model

LOCAL_MODEL = {
    "model_name": "local-llama",
    "litellm_params": {"model": "openai/llama-3", "api_base": "http://localhost:8000"},
}
REMOTE_MODEL = {
    "model_name": "claude-sonnet",
    "litellm_params": {"model": "anthropic/claude-sonnet-4-20250514"},
}


def _config_with_models(*entries):
    return {"model_list": list(entries)}


@pytest.fixture(autouse=True)
def _clean_advisor_env(monkeypatch):
    """Remove AIRLOCK_ADVISOR_MODEL before each test."""
    monkeypatch.delenv("AIRLOCK_ADVISOR_MODEL", raising=False)


class TestSelectAdvisorModel:
    def test_selects_local_when_available(self):
        config = _config_with_models(LOCAL_MODEL)
        name, is_local = select_advisor_model(config)
        assert name == "local-llama"
        assert is_local is True

    def test_prefers_local_over_remote(self):
        config = _config_with_models(REMOTE_MODEL, LOCAL_MODEL)
        name, is_local = select_advisor_model(config)
        assert name == "local-llama"
        assert is_local is True

    def test_selects_remote_when_no_local(self):
        config = _config_with_models(REMOTE_MODEL)
        name, is_local = select_advisor_model(config)
        assert name == "claude-sonnet"
        assert is_local is False

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ADVISOR_MODEL", "claude-sonnet")
        config = _config_with_models(LOCAL_MODEL, REMOTE_MODEL)
        name, is_local = select_advisor_model(config)
        assert name == "claude-sonnet"
        assert is_local is False

    def test_env_override_nonexistent_falls_back(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ADVISOR_MODEL", "no-such-model")
        config = _config_with_models(LOCAL_MODEL)
        name, is_local = select_advisor_model(config)
        assert name == "local-llama"
        assert is_local is True

    def test_empty_model_list_raises(self):
        config = _config_with_models()
        with pytest.raises(ValueError):
            select_advisor_model(config)

    def test_local_only_no_local_raises(self):
        config = _config_with_models(REMOTE_MODEL)
        with pytest.raises(ValueError):
            select_advisor_model(config, local_only=True)

    def test_model_override_parameter(self):
        config = _config_with_models(LOCAL_MODEL, REMOTE_MODEL)
        name, is_local = select_advisor_model(config, model_override="claude-sonnet")
        assert name == "claude-sonnet"
        assert is_local is False

    def test_model_override_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_ADVISOR_MODEL", "claude-sonnet")
        config = _config_with_models(LOCAL_MODEL, REMOTE_MODEL)
        name, is_local = select_advisor_model(config, model_override="local-llama")
        assert name == "local-llama"
        assert is_local is True
