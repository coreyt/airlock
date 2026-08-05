"""Proxy-side MCP startup tool-listing behavior (#20, pack B-3).

Issue #20 was filed against behavior where a slow stdio MCP server logged a
warning at proxy startup and returned an **empty tool list**, so the failure
surfaced to the operator as "the tools vanished" rather than as an error.

On the pinned LiteLLM baseline (1.94.1) that is no longer true: a listing
failure raises a classified ``MCPServerListError`` naming the server, and the
timeout is configurable through ``LITELLM_MCP_TOOL_LISTING_TIMEOUT``. Airlock
therefore needs no fork and no config knob of its own.

These tests exist because Airlock's operator documentation now *depends* on
that upstream contract. A future LiteLLM bump that reintroduces silent-empty
listing would otherwise be invisible until someone noticed missing tools in
production — which is precisely the failure mode the issue described.
"""

from __future__ import annotations

import anyio
import pytest

import litellm.constants
import litellm.proxy._experimental.mcp_server.mcp_server_manager as mcp_manager
from litellm.proxy._experimental.mcp_server.exceptions import MCPServerListError


class _SlowClient:
    """An MCP server that never answers within the deadline."""

    async def list_tools(self, raise_on_error: bool = True):
        await anyio.sleep(60)


class _FailingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def list_tools(self, raise_on_error: bool = True):
        raise self._exc


@pytest.fixture
def fast_timeout(monkeypatch):
    """Shrink the listing deadline so the test is fast and deterministic."""
    monkeypatch.setattr(mcp_manager, "MCP_TOOL_LISTING_TIMEOUT", 0.05)


class TestTimeoutIsNotSilent:
    async def test_slow_server_raises_rather_than_returning_an_empty_list(
        self, fast_timeout
    ):
        """The regression that issue #20 was actually about."""
        manager = mcp_manager.global_mcp_server_manager

        with pytest.raises(MCPServerListError) as excinfo:
            await manager._fetch_tools_with_timeout(_SlowClient(), "newscatcher")

        # Naming the server is the whole point: "tools are missing" is not
        # actionable, "newscatcher timed out" is.
        assert excinfo.value.server_name == "newscatcher"
        assert excinfo.value.fault.tag == "timeout"

    async def test_unreachable_server_is_classified_distinctly(self, fast_timeout):
        manager = mcp_manager.global_mcp_server_manager

        with pytest.raises(MCPServerListError) as excinfo:
            await manager._fetch_tools_with_timeout(
                _FailingClient(ConnectionError("refused")), "tavily"
            )

        assert excinfo.value.server_name == "tavily"
        # A server that is down and a server that is slow call for different
        # operator responses, so they must not collapse into one fault.
        assert excinfo.value.fault.tag == "unreachable"


class TestTimeoutIsConfigurable:
    def test_listing_timeout_comes_from_the_environment(self):
        """No fork needed: LITELLM_MCP_TOOL_LISTING_TIMEOUT is the supported knob.

        Airlock deliberately does not add a competing setting of its own —
        two knobs for one deadline is how they drift apart.
        """
        assert litellm.constants.MCP_TOOL_LISTING_TIMEOUT == pytest.approx(30.0)

    def test_airlock_defines_no_competing_mcp_listing_timeout(self):
        import airlock.proxy as proxy

        source = open(proxy.__file__, encoding="utf-8").read()
        assert "MCP_TOOL_LISTING_TIMEOUT" not in source
