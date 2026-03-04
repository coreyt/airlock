"""
Airlock MCP Tool Guard — access control for MCP tool calls.

Enforces tool-level allowlists and blocklists, plus basic argument
sanitization (path traversal, shell metacharacters).

Env vars:
    AIRLOCK_MCP_ALLOWED_TOOLS — comma-separated allowlist (empty = allow all)
    AIRLOCK_MCP_BLOCKED_TOOLS — comma-separated blocklist
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

logger = logging.getLogger("airlock.guardrails.mcp_tool")

# Shell metacharacters and path traversal patterns to reject
_DANGEROUS_PATTERNS = re.compile(
    r"(?:"
    r"\.\./|"           # path traversal
    r"[;|&`$]|"         # shell metacharacters
    r"\$\(|"            # command substitution
    r">\s*/|"           # redirect to root
    r"<\s*/"            # read from root
    r")"
)


def _allowed_tools() -> list[str]:
    raw = os.getenv("AIRLOCK_MCP_ALLOWED_TOOLS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _blocked_tools() -> list[str]:
    raw = os.getenv("AIRLOCK_MCP_BLOCKED_TOOLS", "")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _check_tool_access(tool_name: str) -> str | None:
    """Return an error message if the tool is not allowed, else None."""
    allowed = _allowed_tools()
    if allowed and tool_name not in allowed:
        return f"Tool '{tool_name}' is not in the allowed tools list."

    blocked = _blocked_tools()
    if tool_name in blocked:
        return f"Tool '{tool_name}' is blocked by policy."

    return None


def _check_arguments(args: Any) -> str | None:
    """Return an error message if any argument value contains dangerous patterns.

    Recurses into nested dicts and lists so structured arguments cannot
    bypass sanitization by wrapping values one level deeper.
    """
    if isinstance(args, str):
        if _DANGEROUS_PATTERNS.search(args):
            return (
                "MCP tool argument contains potentially dangerous content. "
                "Path traversal and shell metacharacters are not allowed."
            )
    elif isinstance(args, dict):
        for value in args.values():
            result = _check_arguments(value)
            if result:
                return result
    elif isinstance(args, list):
        for item in args:
            result = _check_arguments(item)
            if result:
                return result
    return None


class AirlockMCPToolGuard(CustomGuardrail):
    """Pre-MCP-call guardrail for tool access control and argument sanitization."""

    def __init__(self, **kwargs):
        supported_event_hooks = [GuardrailEventHooks.pre_mcp_call]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> dict:
        tool_name = data.get("mcp_tool_name", "")
        if not tool_name:
            return data

        # Tool access control
        error = _check_tool_access(tool_name)
        if error:
            logger.warning("mcp_tool_blocked tool=%r", tool_name)
            raise ValueError(error)

        # Argument sanitization
        args = data.get("mcp_arguments")
        if isinstance(args, dict):
            error = _check_arguments(args)
            if error:
                raise ValueError(error)

        return data
