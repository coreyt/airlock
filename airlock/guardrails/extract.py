"""Unified text extraction for guardrails — handles both LLM and MCP data shapes.

LiteLLM's MCP integration creates synthetic ``messages`` via
``_convert_mcp_to_llm_format()`` and preserves the original tool name and
arguments as ``mcp_tool_name`` and ``mcp_arguments`` in the data dict.

All guardrails should use ``extract_text(data, call_type)`` as the single
entry point so that both LLM completions and MCP tool calls are scanned
through the same pipeline.
"""

from __future__ import annotations

from typing import Any


def extract_text_from_messages(messages: list[dict[str, Any]]) -> str:
    """Flatten LLM message content into a single string."""
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
    return "\n".join(parts)


def _collect_strings(value: Any) -> list[str]:
    """Recursively collect all string representations from a value.

    Handles nested dicts and lists so that keywords and PII buried
    in structured MCP arguments are not invisible to guardrails.
    """
    parts: list[str] = []
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, bool):
        # bool before int — bool is a subclass of int
        parts.append(str(value))
    elif isinstance(value, (int, float)):
        parts.append(str(value))
    elif isinstance(value, dict):
        for v in value.values():
            parts.extend(_collect_strings(v))
    elif isinstance(value, list):
        for item in value:
            parts.extend(_collect_strings(item))
    return parts


def extract_text_from_mcp(data: dict) -> str:
    """Extract scannable text from MCP tool call data.

    Includes tool name and all string values from arguments (including
    nested dicts/lists) so blocked keywords and PII patterns are caught.
    """
    parts: list[str] = []
    tool_name = data.get("mcp_tool_name")
    if tool_name:
        parts.append(str(tool_name))

    args = data.get("mcp_arguments")
    if args is not None:
        parts.extend(_collect_strings(args))

    # Also include synthetic messages if present (LiteLLM generates them)
    messages = data.get("messages")
    if messages:
        msg_text = extract_text_from_messages(messages)
        if msg_text:
            parts.append(msg_text)

    return "\n".join(parts)


def is_mcp_call(data: dict, call_type: str = "") -> bool:
    """Return True if this request is an MCP tool call."""
    if call_type == "call_mcp_tool":
        return True
    return "mcp_tool_name" in data


def extract_text(data: dict, call_type: str = "") -> str:
    """Dispatch: MCP if call_type == 'call_mcp_tool' or 'mcp_tool_name' in data,
    else LLM messages."""
    if is_mcp_call(data, call_type):
        return extract_text_from_mcp(data)
    return extract_text_from_messages(data.get("messages", []))
