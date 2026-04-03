"""
S11 — Custom Providers: Tavily, Perplexity, and NewsCatcher.
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


class TestNewsCatcherMock:

    def test_extract_snippet_with_enrichment(self):
        from airlock.mcp_servers.newscatcher_server import _extract_snippet

        class FakeRecord:
            enrichment = {"key_development": "Company raised $10M Series A"}

        assert "Series A" in _extract_snippet(FakeRecord())

    def test_extract_snippet_fallback_attrs(self):
        from airlock.mcp_servers.newscatcher_server import _extract_snippet

        class FakeRecord:
            enrichment = None
            snippet = "fallback snippet text"

        assert _extract_snippet(FakeRecord()) == "fallback snippet text"

    def test_extract_snippet_empty(self):
        from airlock.mcp_servers.newscatcher_server import _extract_snippet

        class FakeRecord:
            enrichment = None

        assert _extract_snippet(FakeRecord()) == ""

    def test_server_lists_tools(self):
        import asyncio
        from airlock.mcp_servers.newscatcher_server import list_tools

        tools = asyncio.run(list_tools())
        names = {t.name for t in tools}
        assert "newscatcher_search" in names
        assert "newscatcher_search_quick" in names


class TestNewsCatcherLive:

    @pytest.mark.live
    async def test_newscatcher_mcp_search(self, http_client):
        resp = await http_client.post(
            "/v1/mcp/call_tool",
            json={
                "name": "newscatcher_search_quick",
                "arguments": {"query": "Python programming", "max_results": 10},
            },
        )
        assert resp.status_code == 200
