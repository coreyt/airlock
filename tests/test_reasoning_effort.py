"""Tests for per-provider reasoning_effort normalization (the drop_params fix)."""

from __future__ import annotations

import pytest

from airlock.reasoning_effort import normalize_reasoning_effort


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.delenv("AIRLOCK_NORMALIZE_REASONING_EFFORT", raising=False)


class TestOpenAI:
    @pytest.mark.parametrize("val", ["none", "off", "disable", "disabled", "false", "no", "0"])
    def test_off_intent_maps_to_minimal(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "minimal"  # honour intent, not drop->default

    @pytest.mark.parametrize("val", ["minimal", "low", "medium", "high"])
    def test_valid_values_unchanged(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == val

    def test_uppercase_off_intent(self):
        data = {"reasoning_effort": "NONE"}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "minimal"

    def test_unknown_value_left_for_drop_params(self):
        data = {"reasoning_effort": "ultra"}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "ultra"  # not our job to guess

    def test_azure_treated_like_openai(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "azure")
        assert data["reasoning_effort"] == "minimal"


class TestGemini:
    @pytest.mark.parametrize("val", ["none", "off", "minimal"])
    def test_off_intent_and_minimal_map_to_disable(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "gemini")
        assert data["reasoning_effort"] == "disable"

    @pytest.mark.parametrize("val", ["disable", "low", "medium", "high"])
    def test_valid_gemini_values_unchanged(self, val):
        data = {"reasoning_effort": val}
        normalize_reasoning_effort(data, "gemini")
        assert data["reasoning_effort"] == val


class TestAnthropic:
    def test_off_intent_drops_param(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "anthropic")
        assert "reasoning_effort" not in data  # no extended thinking

    def test_real_value_left(self):
        data = {"reasoning_effort": "low"}
        normalize_reasoning_effort(data, "anthropic")
        assert data["reasoning_effort"] == "low"


class TestMisc:
    def test_absent_is_noop(self):
        data = {"model": "gpt-5.4"}
        normalize_reasoning_effort(data, "openai")
        assert "reasoning_effort" not in data

    def test_unknown_provider_unchanged(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "mistral")
        assert data["reasoning_effort"] == "none"

    def test_none_provider_unchanged(self):
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, None)
        assert data["reasoning_effort"] == "none"

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_NORMALIZE_REASONING_EFFORT", "0")
        data = {"reasoning_effort": "none"}
        normalize_reasoning_effort(data, "openai")
        assert data["reasoning_effort"] == "none"  # toggle off -> no change

    def test_returns_data_for_chaining(self):
        data = {"reasoning_effort": "none"}
        assert normalize_reasoning_effort(data, "openai") is data
