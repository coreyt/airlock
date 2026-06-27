"""Tests for airlock/capability.py — shared served-by provider classifier."""

from __future__ import annotations

import pytest

from airlock.capability import airlock_provider_for, normalize_provider_token


def _entry(model: str, **params) -> dict:
    lp = {"model": model}
    lp.update(params)
    return {"litellm_params": lp}


class TestNormalizeProviderToken:
    def test_aistudio_to_gemini(self):
        assert normalize_provider_token("aistudio") == "gemini"

    def test_vertex_to_vertex_ai(self):
        assert normalize_provider_token("vertex") == "vertex_ai"

    def test_vertex_ai_beta_to_vertex_ai(self):
        assert normalize_provider_token("vertex_ai_beta") == "vertex_ai"

    @pytest.mark.parametrize(
        "token",
        [
            "gemini",
            "vertex_ai",
            "openai",
            "anthropic",
            "mistral",
            "perplexity",
            "tavily",
        ],
    )
    def test_native_tokens_pass_through(self, token):
        assert normalize_provider_token(token) == token

    def test_unknown_identity(self):
        assert normalize_provider_token("weird") == "weird"


class TestAirlockProviderFor:
    def test_anthropic(self):
        assert airlock_provider_for(_entry("anthropic/claude-opus-4-8")) == "anthropic"

    def test_openai(self):
        assert airlock_provider_for(_entry("openai/gpt-5.5")) == "openai"

    def test_gemini_aistudio_entry(self):
        # alias `aistudio/gemini-3.5-flash` -> litellm model gemini/...
        assert airlock_provider_for(_entry("gemini/gemini-3.5-flash")) == "gemini"

    def test_vertex(self):
        assert airlock_provider_for(_entry("vertex_ai/gemini-3.5-flash")) == "vertex_ai"

    def test_vertex_ai_beta_normalized(self):
        assert (
            airlock_provider_for(_entry("vertex_ai_beta/gemini-3.5-flash"))
            == "vertex_ai"
        )

    def test_mistral(self):
        assert airlock_provider_for(_entry("mistral/mistral-large-latest")) == "mistral"

    def test_perplexity(self):
        assert airlock_provider_for(_entry("perplexity/sonar")) == "perplexity"

    def test_tavily(self):
        assert airlock_provider_for(_entry("tavily/web-search")) == "tavily"

    def test_vllm_is_openai(self):
        assert (
            airlock_provider_for(
                _entry("openai/qwen3.6-27b", api_base="http://host:8000/v1")
            )
            == "openai"
        )

    def test_enhanced_resolves_to_target_gemini(self):
        entry = {
            "litellm_params": {
                "model": "enhanced/gemini-coding",
                "enhanced_profile": {
                    "target_model": "gemini/gemini-3.1-pro-preview-customtools",
                },
            }
        }
        assert airlock_provider_for(entry) == "gemini"

    def test_enhanced_never_returns_enhanced(self):
        entry = {
            "litellm_params": {
                "model": "enhanced/gemini-coding",
                "enhanced_profile": {
                    "target_model": "gemini/gemini-3.1-pro-preview-customtools",
                },
            }
        }
        assert airlock_provider_for(entry) != "enhanced"

    def test_missing_model_returns_none(self):
        assert airlock_provider_for({"litellm_params": {}}) is None
        assert airlock_provider_for({}) is None
