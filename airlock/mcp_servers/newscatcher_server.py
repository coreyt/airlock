"""NewsCatcher CatchAll MCP server — stdio transport.

Wraps the newscatcher-catchall-sdk as an MCP tool server so LiteLLM can
call it via stdio transport.  Exposes two tools:

  - newscatcher_search: Submit a search query, poll for results, return records
  - newscatcher_search_quick: Submit a search with shorter timeout (60s)

Requires NEWS_CATCHER_API_KEY in the environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    raise SystemExit(
        "mcp package required: pip install mcp"
    )

_DEFAULT_POLL_TIMEOUT = 180  # 3 minutes
_QUICK_POLL_TIMEOUT = 60  # 1 minute
_POLL_INTERVAL = 10  # seconds
_INITIAL_POLL_DELAY = 3  # shorter first poll

server = Server("newscatcher")

# Module-level client cache to avoid per-call instantiation
_client = None


def _get_client():
    """Lazy-init and cache the CatchAll client."""
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("NEWS_CATCHER_API_KEY", "")
    if not api_key:
        raise ValueError("NEWS_CATCHER_API_KEY environment variable is required")
    from newscatcher_catchall import CatchAllApi
    _client = CatchAllApi(api_key=api_key)
    return _client


def _extract_snippet(record) -> str:
    """Extract a useful snippet from a CatchAll record."""
    enrichment = getattr(record, "enrichment", None)
    if enrichment:
        # Handle both dict and object-style enrichment
        if isinstance(enrichment, dict):
            key_dev = enrichment.get("key_development", "")
        else:
            key_dev = getattr(enrichment, "key_development", "")
        if key_dev:
            return str(key_dev)
    for attr in ("snippet", "text_preview", "description", "summary"):
        val = getattr(record, attr, None)
        if val:
            return str(val)
    return ""


async def _do_search(query: str, max_results: int, poll_timeout: int) -> str:
    """Submit a CatchAll job, poll for results, return formatted text."""
    client = _get_client()
    api_limit = max(max_results, 10)  # CatchAll minimum is 10

    job = await asyncio.to_thread(client.jobs.create_job, query=query, limit=api_limit)
    job_id = getattr(job, "job_id", None)
    if not job_id:
        return "NewsCatcher API error: no job_id returned from create_job."
    logger.info("NewsCatcher job %s created for query=%.60r", job_id, query)

    deadline = time.monotonic() + poll_timeout
    completed = False
    first_poll = True
    while time.monotonic() < deadline:
        delay = _INITIAL_POLL_DELAY if first_poll else _POLL_INTERVAL
        first_poll = False
        await asyncio.sleep(delay)
        try:
            status = await asyncio.to_thread(client.jobs.get_job_status, job_id)
        except Exception as exc:
            logger.warning("Transient error polling job %s: %s", job_id, exc)
            continue
        state = getattr(status, "status", "unknown")
        if state == "completed":
            completed = True
            break
        if state in ("failed", "error"):
            return f"NewsCatcher job {job_id} failed with status: {state}"

    if not completed:
        return (
            f"NewsCatcher job {job_id} did not complete within {poll_timeout}s. "
            f"The job is still running — results are not yet available."
        )

    results = await asyncio.to_thread(client.jobs.get_job_results, job_id)
    records = getattr(results, "all_records", []) or []

    if not records:
        return "No results found."

    parts = []
    for i, r in enumerate(records[:max_results], 1):
        title = getattr(r, "record_title", "") or ""
        url = getattr(r, "url", "") or getattr(r, "link", "") or ""
        snippet = _extract_snippet(r)
        parts.append(f"[{i}] {title}\n    {url}\n    {snippet}")

    return "\n\n".join(parts)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="newscatcher_search",
            description=(
                "Search the web using NewsCatcher CatchAll API. "
                "Returns validated, NLP-enriched results. "
                "Jobs may take 2-10 minutes to complete."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 10, min 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="newscatcher_search_quick",
            description=(
                "Quick NewsCatcher search with 60-second timeout. "
                "Returns a timeout notice if the job hasn't completed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 10, min 10)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    query = arguments.get("query", "")
    if not query or not query.strip():
        return [TextContent(type="text", text="Error: query is required and cannot be empty.")]

    max_results = arguments.get("max_results", 10)

    if name == "newscatcher_search":
        timeout = _DEFAULT_POLL_TIMEOUT
    elif name == "newscatcher_search_quick":
        timeout = _QUICK_POLL_TIMEOUT
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    try:
        result = await _do_search(query.strip(), max_results, timeout)
    except Exception as e:
        logger.exception("NewsCatcher search failed for query=%.60r", query)
        result = f"Error: {type(e).__name__}: {e}"

    return [TextContent(type="text", text=result)]


async def _main():
    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
