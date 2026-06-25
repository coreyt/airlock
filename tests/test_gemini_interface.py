from __future__ import annotations

from airlock.gemini_interface import (
    apply_gemini_request_semantics,
    build_gemini_response_headers,
    classify_gemini_response_body,
)


class TestApplyGeminiRequestSemantics:
    def test_text_only_maps_to_disable_reasoning(self):
        data = {
            "model": "gemini-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"airlock": {"gemini": {"mode": "text_only"}}},
        }
        result = apply_gemini_request_semantics(data, provider="gemini")
        assert result["reasoning_effort"] == "disable"
        assert result["metadata"]["airlock_gemini"]["mode"] == "text_only"
        assert (
            result["metadata"]["airlock_gemini"]["mapping_source"] == "airlock_semantic"
        )

    def test_explicit_controls_win(self):
        data = {
            "model": "gemini-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "reasoning_effort": "high",
            "metadata": {"airlock": {"gemini": {"mode": "text_only"}}},
        }
        result = apply_gemini_request_semantics(data, provider="gemini")
        assert result["reasoning_effort"] == "high"
        assert (
            result["metadata"]["airlock_gemini"]["mapping_source"] == "client_explicit"
        )
        assert "warnings" in result["metadata"]["airlock_gemini"]


def _ledger(data):
    return data.get("metadata", {}).get("airlock_mutations", [])


class TestGeminiLedger:
    def test_deep_reasoning_records_set_high(self):
        data = {
            "model": "gemini-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"airlock": {"gemini": {"mode": "deep_reasoning"}}},
        }
        apply_gemini_request_semantics(data, provider="gemini")
        muts = [m for m in _ledger(data) if m.field == "reasoning_effort"]
        assert len(muts) == 1
        assert muts[0].op == "set"
        assert muts[0].before is None
        assert muts[0].after == "high"
        assert muts[0].stage == "pre_call"
        assert muts[0].source == "gemini.mode"

    def test_text_only_records_set_disable(self):
        data = {
            "model": "gemini-pro",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"airlock": {"gemini": {"mode": "text_only"}}},
        }
        apply_gemini_request_semantics(data, provider="gemini")
        muts = [m for m in _ledger(data) if m.field == "reasoning_effort"]
        assert len(muts) == 1
        assert muts[0].op == "set"
        assert muts[0].after == "disable"

    def test_explicit_controls_records_nothing(self):
        data = {
            "model": "gemini-pro",
            "reasoning_effort": "high",
            "metadata": {"airlock": {"gemini": {"mode": "text_only"}}},
        }
        apply_gemini_request_semantics(data, provider="gemini")
        assert _ledger(data) == []

    def test_non_gemini_records_nothing(self):
        data = {"model": "claude-sonnet"}
        apply_gemini_request_semantics(data, provider="anthropic")
        assert _ledger(data) == []


class TestClassifyGeminiResponseBody:
    def test_classifies_thought_only_success(self):
        body = {
            "choices": [{"message": {"content": None}, "finish_reason": "length"}],
            "usage": {
                "completion_tokens_details": {"reasoning_tokens": 8, "text_tokens": 0}
            },
        }
        result = classify_gemini_response_body(body)
        assert result["output_shape"] == "thought_only"
        assert result["empty_text_success"] is True

    def test_classifies_text_success(self):
        body = {
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens_details": {"text_tokens": 1}},
        }
        result = classify_gemini_response_body(body)
        assert result["output_shape"] == "text"
        assert result["empty_text_success"] is False

    def test_build_headers(self):
        headers = build_gemini_response_headers(
            {"mode": "deep_reasoning"},
            {"output_shape": "thought_only", "empty_text_success": True},
        )
        assert headers["X-Airlock-Provider-Mode"] == "gemini"
        assert headers["X-Airlock-Reasoning-Mode"] == "deep_reasoning"
        assert headers["X-Airlock-Provider-State"] == "thought_only"
        assert headers["X-Airlock-Empty-Text-Success"] == "true"
