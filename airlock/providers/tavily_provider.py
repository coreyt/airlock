"""Tavily web search exposed as a LiteLLM custom chat completion provider.

Clients send ``model: tavily-search`` (or ``tavily/web-search``) and get
back a chat-style response whose content is formatted search results with
titles, URLs, and snippets.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable, Optional, Union

import httpx
from litellm.llms.custom_llm import CustomLLM
from litellm.types.utils import Choices, Message, ModelResponse, Usage

logger = logging.getLogger("airlock.providers.tavily")


def _extract_query(messages: list) -> str:
    """Pull the user query from the last message."""
    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            # Handle multimodal list-of-dicts content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                return " ".join(parts).strip()
            return str(content).strip()
    return ""


def _format_results(results: list[dict]) -> str:
    """Format Tavily search results into readable text."""
    if not results:
        return "No search results found."
    parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = r.get("content", "")
        parts.append(f"[{i}] {title}\n    {url}\n    {content}")
    return "\n\n".join(parts)


def _build_response(
    model: str,
    text: str,
    model_response: ModelResponse,
    prompt_tokens: int,
) -> ModelResponse:
    """Populate a ModelResponse with the search results."""
    model_response.id = f"tavily-{uuid.uuid4().hex[:12]}"
    model_response.model = model
    model_response.created = int(time.time())
    model_response.choices = [  # type: ignore[assignment]
        Choices(
            index=0,
            message=Message(role="assistant", content=text),
            finish_reason="stop",
        )
    ]
    # Approximate token counts (~4 chars per token)
    completion_tokens = len(text) // 4 or 1
    model_response.usage = Usage(  # type: ignore[assignment]
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return model_response


def _do_search(api_key: str, query: str, max_results: int) -> str:
    """Run the Tavily search and return formatted text."""
    from tavily import TavilyClient

    if not api_key:
        raise ValueError("Tavily API key is required. Set TAVILY_API_KEY in .env.")

    tavily = TavilyClient(api_key=api_key)
    try:
        response = tavily.search(query=query, max_results=max_results)
    except Exception as exc:
        logger.error("Tavily search failed: %s", exc)
        raise ValueError(f"Tavily search failed: {exc}") from exc

    text = _format_results(response.get("results", []))
    answer = response.get("answer")
    if answer:
        text = f"**Summary:** {answer}\n\n---\n\n{text}"
    return text


class TavilySearchProvider(CustomLLM):
    """LiteLLM custom provider that wraps TavilyClient.search()."""

    def completion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding,
        api_key,
        logging_obj,
        optional_params: dict,
        acompletion=None,
        litellm_params=None,
        logger_fn=None,
        headers=None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client=None,
    ) -> ModelResponse:
        query = _extract_query(messages)
        if not query:
            raise ValueError("No user message found in request.")

        max_results = optional_params.get("max_results", 5)
        text = _do_search(api_key, query, max_results)
        prompt_tokens = len(query) // 4 or 1
        return _build_response(model, text, model_response, prompt_tokens)

    async def acompletion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding,
        api_key,
        logging_obj,
        optional_params: dict,
        acompletion=None,
        litellm_params=None,
        logger_fn=None,
        headers=None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client=None,
    ) -> ModelResponse:
        import asyncio

        query = _extract_query(messages)
        if not query:
            raise ValueError("No user message found in request.")

        max_results = optional_params.get("max_results", 5)
        text = await asyncio.to_thread(_do_search, api_key, query, max_results)
        prompt_tokens = len(query) // 4 or 1
        return _build_response(model, text, model_response, prompt_tokens)


# Module-level instance — LiteLLM's get_instance_fn resolves to this.
tavily_handler = TavilySearchProvider()
