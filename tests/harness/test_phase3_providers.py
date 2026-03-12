"""
S11 — Custom Providers: Tavily and Perplexity.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.harness


class TestTavilyMock:

    def test_extract_query(self):
        from airlock.providers.tavily_provider import _extract_query

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "latest Python release"},
        ]
        assert _extract_query(messages) == "latest Python release"

    def test_extract_query_multipart(self):
        from airlock.providers.tavily_provider import _extract_query

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "search for this"},
                ],
            }
        ]
        assert _extract_query(messages) == "search for this"

    def test_extract_query_empty_returns_empty(self):
        from airlock.providers.tavily_provider import _extract_query

        assert _extract_query([]) == ""

    def test_build_response_shape(self):
        from airlock.providers.tavily_provider import TavilySearchProvider

        provider = TavilySearchProvider()
        # Verify the class has the expected interface
        assert hasattr(provider, "completion")
        assert hasattr(provider, "acompletion")


class TestTavilyLive:

    @pytest.mark.live
    async def test_tavily_live_search(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "tavily-search",
                "messages": [{"role": "user", "content": "latest Python release"}],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        assert len(content) > 0

    @pytest.mark.live
    async def test_tavily_response_has_usage(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "tavily-search",
                "messages": [{"role": "user", "content": "Python 3.13"}],
            },
        )
        body = resp.json()
        assert body["usage"]["prompt_tokens"] > 0


class TestPerplexityLive:

    @pytest.mark.live
    async def test_perplexity_live_search(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "perplexity-sonar",
                "messages": [{"role": "user", "content": "What is Python?"}],
                "max_tokens": 50,
            },
        )
        assert resp.status_code == 200

    @pytest.mark.live
    async def test_perplexity_response_nonempty(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "perplexity-sonar",
                "messages": [{"role": "user", "content": "What is 1+1?"}],
                "max_tokens": 10,
            },
        )
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        assert len(content) > 0
