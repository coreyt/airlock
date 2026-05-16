"""Tests for airlock.guardrails.local_vllm_router."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from airlock.guardrails import local_vllm_router as lvr
from airlock.guardrails.local_vllm_router import (
    AirlockLocalVLLMRouter,
    _load_alias_map,
    _strip_provider,
)


BASE = "http://192.168.1.45:8000/v1"


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "model_list": [
                    # Two local-vLLM entries
                    {
                        "model_name": "kimi-dev",
                        "litellm_params": {
                            "model": "openai/kimi-dev-72b",
                            "api_base": BASE,
                            "api_key": "x",
                        },
                    },
                    {
                        "model_name": "qwen3.6-27b",
                        "litellm_params": {
                            "model": "openai/qwen3.6-27b",
                            "api_base": BASE,
                            "api_key": "x",
                        },
                    },
                    # A cloud entry (should be ignored)
                    {
                        "model_name": "claude-opus",
                        "litellm_params": {
                            "model": "anthropic/claude-opus-4-6",
                            "api_key": "x",
                        },
                    },
                ]
            }
        )
    )
    return path


@pytest.fixture
def router(monkeypatch, config_file):
    monkeypatch.setenv("AIRLOCK_LOCAL_VLLM_BASE_URL", BASE)
    monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
    monkeypatch.setenv("AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS", "0")  # disable cache in tests
    monkeypatch.delenv("AIRLOCK_LOCAL_VLLM_SWITCH_HINT", raising=False)
    return AirlockLocalVLLMRouter(guardrail_name="airlock-local-vllm-router")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    def test_strip_provider_with_prefix(self):
        assert _strip_provider("openai/kimi-dev-72b") == "kimi-dev-72b"

    def test_strip_provider_no_prefix(self):
        assert _strip_provider("qwen3-32b") == "qwen3-32b"

    def test_load_alias_map_filters_by_base_url(self, config_file):
        m = _load_alias_map(str(config_file), BASE)
        assert m == {"kimi-dev": "kimi-dev-72b", "qwen3.6-27b": "qwen3.6-27b"}

    def test_load_alias_map_handles_trailing_slash(self, config_file):
        # Adding a trailing slash to the requested base should still match.
        m = _load_alias_map(str(config_file), BASE + "/")
        assert "kimi-dev" in m

    def test_load_alias_map_missing_file_returns_empty(self, tmp_path):
        assert _load_alias_map(str(tmp_path / "nope.yaml"), BASE) == {}


# ---------------------------------------------------------------------------
# pre-call hook
# ---------------------------------------------------------------------------
class _FakeKey:
    pass


def _stub_loaded(router_obj: AirlockLocalVLLMRouter, names: set[str]) -> None:
    router_obj._loaded_models = AsyncMock(return_value=names)  # type: ignore[assignment]


class TestPreCall:
    @pytest.mark.asyncio
    async def test_passthrough_for_non_local_alias(self, router):
        _stub_loaded(router, {"qwen3.6-27b"})
        data: dict[str, Any] = {"model": "claude-opus"}
        out = await router.async_pre_call_hook(_FakeKey(), None, data, "chat_completion")
        assert out is data

    @pytest.mark.asyncio
    async def test_passthrough_when_local_alias_matches_loaded(self, router):
        _stub_loaded(router, {"qwen3.6-27b"})
        data = {"model": "qwen3.6-27b"}
        out = await router.async_pre_call_hook(_FakeKey(), None, data, "chat_completion")
        assert out is data

    @pytest.mark.asyncio
    async def test_blocks_when_local_alias_not_loaded(self, router):
        _stub_loaded(router, {"qwen3.6-27b"})
        data = {"model": "kimi-dev"}
        with pytest.raises(ValueError) as exc:
            await router.async_pre_call_hook(_FakeKey(), None, data, "chat_completion")
        msg = str(exc.value)
        assert "kimi-dev" in msg
        assert "kimi-dev-72b" in msg
        assert "qwen3.6-27b" in msg
        assert BASE in msg

    @pytest.mark.asyncio
    async def test_blocks_when_vllm_unreachable(self, router):
        _stub_loaded(router, set())
        data = {"model": "kimi-dev"}
        with pytest.raises(ValueError) as exc:
            await router.async_pre_call_hook(_FakeKey(), None, data, "chat_completion")
        assert "unreachable" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_passthrough_when_model_empty(self, router):
        data = {"model": ""}
        out = await router.async_pre_call_hook(_FakeKey(), None, data, "chat_completion")
        assert out is data

    @pytest.mark.asyncio
    async def test_custom_switch_hint(self, router, monkeypatch):
        monkeypatch.setenv(
            "AIRLOCK_LOCAL_VLLM_SWITCH_HINT",
            "docker stop X && start-{requested}.sh",
        )
        _stub_loaded(router, {"qwen3.6-27b"})
        data = {"model": "kimi-dev"}
        with pytest.raises(ValueError) as exc:
            await router.async_pre_call_hook(_FakeKey(), None, data, "chat_completion")
        assert "start-kimi-dev.sh" in str(exc.value)


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------
class TestCaching:
    @pytest.mark.asyncio
    async def test_loaded_models_cached_within_ttl(self, monkeypatch, config_file):
        monkeypatch.setenv("AIRLOCK_LOCAL_VLLM_BASE_URL", BASE)
        monkeypatch.setenv("AIRLOCK_CONFIG", str(config_file))
        monkeypatch.setenv("AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS", "60")
        router = AirlockLocalVLLMRouter(guardrail_name="x")

        call_count = {"n": 0}

        async def fake_get(self, url):  # noqa: ARG001
            call_count["n"] += 1
            return _FakeResp({"data": [{"id": "kimi-dev-72b"}]})

        monkeypatch.setattr(lvr.httpx.AsyncClient, "get", fake_get)
        assert "kimi-dev-72b" in await router._loaded_models()
        assert "kimi-dev-72b" in await router._loaded_models()
        assert call_count["n"] == 1


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload
