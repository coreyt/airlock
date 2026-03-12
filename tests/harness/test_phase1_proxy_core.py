"""
S2 — Proxy Core: health, completions, model listing, config parsing.

Live tests hit a real proxy. Mock tests validate config structure.
"""

from __future__ import annotations

import pytest
import yaml


pytestmark = pytest.mark.harness


# ---------------------------------------------------------------------------
# Live tests (require --run-live)
# ---------------------------------------------------------------------------
class TestProxyCoreLive:

    @pytest.mark.live
    async def test_health_authenticated(self, http_client):
        resp = await http_client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.live
    async def test_health_unauthenticated_401(self, proxy_url):
        import httpx

        async with httpx.AsyncClient(base_url=proxy_url, timeout=10) as c:
            resp = await c.get("/health")
        assert resp.status_code == 401

    @pytest.mark.live
    async def test_completion_response_shape(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-haiku",
                "messages": [{"role": "user", "content": "Say hello"}],
                "max_tokens": 10,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "id" in body
        assert "choices" in body
        assert "usage" in body

    @pytest.mark.live
    async def test_streaming_returns_sse(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-haiku",
                "messages": [{"role": "user", "content": "Count to 3"}],
                "stream": True,
                "max_tokens": 20,
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @pytest.mark.live
    async def test_streaming_done_sentinel(self, http_client):
        import httpx

        async with httpx.AsyncClient(
            base_url=str(http_client.base_url),
            headers=dict(http_client.headers),
            timeout=30,
        ) as c:
            async with c.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": "claude-haiku",
                    "messages": [{"role": "user", "content": "Say hi"}],
                    "stream": True,
                    "max_tokens": 10,
                },
            ) as resp:
                lines = []
                async for line in resp.aiter_lines():
                    lines.append(line)
        assert any("[DONE]" in line for line in lines)

    @pytest.mark.live
    async def test_model_listing_includes_families(self, http_client):
        resp = await http_client.get("/v1/models")
        assert resp.status_code == 200
        model_ids = [m["id"] for m in resp.json()["data"]]
        assert any("claude" in m for m in model_ids)

    @pytest.mark.live
    async def test_unsupported_param_dropped(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-haiku",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
                "foo_unsupported": "bar",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Mock tests (config parsing, no proxy)
# ---------------------------------------------------------------------------
class TestProxyConfigMock:

    def test_config_has_drop_params(self, harness_config):
        config = yaml.safe_load(harness_config.read_text())
        assert config["litellm_settings"]["drop_params"] is True

    def test_config_model_list_not_empty(self, harness_config):
        config = yaml.safe_load(harness_config.read_text())
        assert len(config["model_list"]) > 0

    def test_config_general_settings_master_key(self, harness_config):
        config = yaml.safe_load(harness_config.read_text())
        assert "master_key" in config["general_settings"]
