"""Tests for airlock/capability.py — shared served-by provider classifier."""

from __future__ import annotations

import pytest

from airlock.capability import (
    airlock_provider_for,
    capability_record,
    endpoints_for,
    normalize_provider_token,
)


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


class TestEndpointsFor:
    def test_plain_entry_chat_only(self):
        assert endpoints_for(_entry("gemini/gemini-3.5-flash")) == ["chat"]

    def test_anthropic_chat_only(self):
        assert endpoints_for(_entry("anthropic/claude-opus-4-8")) == ["chat"]

    def test_airlock_batch_marker_aistudio(self):
        entry = {
            "model_name": "aistudio/gemini-3.5-flash",
            "litellm_params": {"model": "gemini/gemini-3.5-flash"},
            "airlock_batch": {"backend": "aistudio", "provider_model": "x"},
        }
        assert endpoints_for(entry) == ["chat", "batch"]

    def test_airlock_batch_marker_mistral(self):
        entry = {
            "litellm_params": {"model": "mistral/mistral-large-latest"},
            "airlock_batch": {"backend": "mistral"},
        }
        assert endpoints_for(entry) == ["chat", "batch"]

    def test_airlock_batch_marker_vllm(self):
        entry = {
            "litellm_params": {"model": "openai/qwen3.6-27b"},
            "airlock_batch": {"backend": "vllm"},
        }
        assert endpoints_for(entry) == ["chat", "batch"]

    def test_vertex_global_is_chat_only(self):
        entry = _entry("vertex_ai/gemini-3.5-flash", vertex_location="global")
        assert endpoints_for(entry) == ["chat"]

    def test_vertex_global_uppercase_is_chat_only(self):
        entry = _entry("vertex_ai/gemini-3.5-flash", vertex_location="GLOBAL")
        assert endpoints_for(entry) == ["chat"]

    def test_vertex_regional_gets_batch(self):
        entry = _entry("vertex_ai/gemini-3.5-flash", vertex_location="us-central1")
        assert endpoints_for(entry) == ["chat", "batch"]

    def test_vertex_no_location_chat_only(self):
        assert endpoints_for(_entry("vertex_ai/gemini-3.5-flash")) == ["chat"]

    def test_falsy_airlock_batch_chat_only(self):
        entry = {
            "litellm_params": {"model": "gemini/gemini-3.5-flash"},
            "airlock_batch": None,
        }
        assert endpoints_for(entry) == ["chat"]

    def test_empty_model_chat_only(self):
        assert endpoints_for({"litellm_params": {}}) == ["chat"]
        assert endpoints_for({}) == ["chat"]


class TestCapabilityRecord:
    def test_anthropic_bare(self):
        entry = {
            "model_name": "claude-opus",
            "litellm_params": {"model": "anthropic/claude-opus-4-8"},
        }
        rec = capability_record(entry)
        assert rec == {
            "airlock_provider": "anthropic",
            "endpoints": ["chat"],
            "underlying": "anthropic/claude-opus-4-8",
            "region": None,
            "deprecated": False,
        }

    def test_aistudio_batch_marker(self):
        entry = {
            "model_name": "aistudio/gemini-3.5-flash",
            "litellm_params": {"model": "gemini/gemini-3.5-flash"},
            "airlock_batch": {"backend": "aistudio"},
        }
        rec = capability_record(entry)
        assert rec["airlock_provider"] == airlock_provider_for(entry)
        assert rec["airlock_provider"] == "gemini"
        assert rec["underlying"] == "gemini/gemini-3.5-flash"
        assert rec["endpoints"] == endpoints_for(entry)
        assert rec["endpoints"] == ["chat", "batch"]
        assert rec["region"] is None
        assert rec["deprecated"] is False

    def test_vertex_global_region_and_chat_only(self):
        entry = {
            "model_name": "gemini-3.5-flash-vertex",
            "litellm_params": {
                "model": "vertex_ai/gemini-3.5-flash",
                "vertex_location": "global",
            },
        }
        rec = capability_record(entry)
        assert rec["airlock_provider"] == "vertex_ai"
        assert rec["region"] == "global"
        assert rec["endpoints"] == ["chat"]
        assert rec["deprecated"] is True

    @pytest.mark.parametrize(
        "model_name",
        [
            "gemini-3.5-flash-aistudio",
            "gemini-3.1-pro-aistudio",
            "gemini-3.5-flash-vertex",
            "gemini-3.1-pro-vertex",
            "mistral-large-batch",
            "mistral-small-batch",
            "qwen36-27b-vllm-batch",
        ],
    )
    def test_suffix_twins_deprecated(self, model_name):
        entry = {
            "model_name": model_name,
            "litellm_params": {"model": "gemini/gemini-3.5-flash"},
        }
        assert capability_record(entry)["deprecated"] is True

    @pytest.mark.parametrize(
        "model_name",
        [
            "gemini-3.5-flash",
            "aistudio/gemini-3.5-flash",
            "mistral/mistral-large",
            "vertex/gemini-3.1-pro",
        ],
    )
    def test_bare_and_provider_aliases_not_deprecated(self, model_name):
        entry = {
            "model_name": model_name,
            "litellm_params": {"model": "gemini/gemini-3.5-flash"},
        }
        assert capability_record(entry)["deprecated"] is False

    def test_empty_litellm_params_safe(self):
        rec = capability_record({"model_name": "weird"})
        assert rec["airlock_provider"] is None
        assert rec["endpoints"] == ["chat"]
        assert rec["underlying"] is None
        assert rec["region"] is None
        assert rec["deprecated"] is False

    def test_record_has_exact_keys(self):
        rec = capability_record(
            {"model_name": "x", "litellm_params": {"model": "openai/gpt-5.5"}}
        )
        assert set(rec) == {
            "airlock_provider",
            "endpoints",
            "underlying",
            "region",
            "deprecated",
        }
