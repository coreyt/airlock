"""Unit tests — vLLM batch translation (Slice 0; pure, no network)."""

from __future__ import annotations

from airlock.batch.vllm import openai_line_to_vllm, vllm_result_to_openai


class TestToVllm:
    def test_keeps_envelope_and_rewrites_model(self):
        line = {
            "custom_id": "r1",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {"model": "qwen36-27b-vllm-batch", "messages": [{"role": "user"}]},
        }
        out = openai_line_to_vllm(line, "qwen3.6-27b")
        assert out["custom_id"] == "r1"
        assert "method" not in out and "url" not in out
        assert out["body"]["model"] == "qwen3.6-27b"  # alias -> served id
        assert out["body"]["messages"] == [{"role": "user"}]

    def test_none_provider_model_leaves_body_model(self):
        line = {"custom_id": "r1", "body": {"model": "x"}}
        assert openai_line_to_vllm(line, None)["body"]["model"] == "x"

    def test_key_alias_for_custom_id(self):
        assert openai_line_to_vllm({"key": "k1", "body": {}}, "m")["custom_id"] == "k1"


class TestFromVllm:
    def test_success_preserves_body_verbatim(self):
        native = {
            "custom_id": "r1",
            "response": {
                "status_code": 200,
                "body": {
                    "id": "cmpl-1",
                    "choices": [{"message": {"content": "PONG"}}],
                },
            },
        }
        out = vllm_result_to_openai(native)
        assert out["custom_id"] == "r1"
        assert out["error"] is None
        assert out["response"]["body"]["choices"][0]["message"]["content"] == "PONG"

    def test_error_line(self):
        native = {
            "custom_id": "r2",
            "error": {"code": "execution_error", "message": "boom"},
        }
        out = vllm_result_to_openai(native)
        assert out["response"] is None
        assert out["error"]["message"] == "boom"

    def test_choices_key_always_present(self):
        native = {
            "custom_id": "r3",
            "response": {"status_code": 200, "body": {"id": "x"}},
        }
        assert vllm_result_to_openai(native)["response"]["body"]["choices"] == []
