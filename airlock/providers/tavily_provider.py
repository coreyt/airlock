"""Tavily web search exposed as a LiteLLM custom chat completion provider.

Clients send ``model: tavily-search`` (or ``tavily/web-search``) and get
back a chat-style response whose content is formatted search results with
titles, URLs, and snippets.
"""

from __future__ import annotations

import time
import uuid
from typing import Callable, Optional, Union

import httpx
from litellm.llms.custom_llm import CustomLLM
from litellm.types.utils import Choices, Message, ModelResponse, Usage


def _extract_query(messages: list) -> str:
    """Pull the user query from the last message."""
    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            return content.strip()
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
    # Approximate token counts (Tavily doesn't report tokens)
    completion_tokens = len(text.split())
    model_response.usage = Usage(  # type: ignore[assignment]
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return model_response


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
        headers={},
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client=None,
    ) -> ModelResponse:
        from tavily import TavilyClient

        query = _extract_query(messages)
        if not query:
            return _build_response(model, "No query provided.", model_response, 0)

        max_results = optional_params.get("max_results", 5)
        tavily = TavilyClient(api_key=api_key)
        response = tavily.search(query=query, max_results=max_results)
        text = _format_results(response.get("results", []))

        # Include the answer summary if Tavily provides one
        answer = response.get("answer")
        if answer:
            text = f"**Summary:** {answer}\n\n---\n\n{text}"

        prompt_tokens = len(query.split())
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
        headers={},
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client=None,
    ) -> ModelResponse:
        import asyncio

        from tavily import TavilyClient

        query = _extract_query(messages)
        if not query:
            return _build_response(model, "No query provided.", model_response, 0)

        max_results = optional_params.get("max_results", 5)
        tavily = TavilyClient(api_key=api_key)
        response = await asyncio.to_thread(
            tavily.search, query=query, max_results=max_results
        )
        text = _format_results(response.get("results", []))

        answer = response.get("answer")
        if answer:
            text = f"**Summary:** {answer}\n\n---\n\n{text}"

        prompt_tokens = len(query.split())
        return _build_response(model, text, model_response, prompt_tokens)


# Module-level instance — LiteLLM's get_instance_fn resolves to this.
tavily_handler = TavilySearchProvider()
