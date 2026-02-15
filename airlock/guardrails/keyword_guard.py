"""
Airlock Keyword Guard — blocks requests that contain restricted keywords
or phrases (project codenames, classified terms, etc.).

Env vars:
    AIRLOCK_BLOCKED_KEYWORDS — comma-separated list of blocked phrases
        (case-insensitive matching)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

logger = logging.getLogger("airlock.guardrails.keyword")


def _blocked_keywords() -> list[str]:
    raw = os.getenv("AIRLOCK_BLOCKED_KEYWORDS", "")
    return [kw.strip().lower() for kw in raw.split(",") if kw.strip()]


def _extract_text(messages: list[dict[str, Any]]) -> str:
    """Flatten all message content into a single lowercase string for scanning."""
    parts: list[str] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
    return "\n".join(parts).lower()


class AirlockKeywordGuard(CustomGuardrail):
    """Pre-call guardrail that rejects prompts containing blocked keywords."""

    def __init__(self, **kwargs):
        supported_event_hooks = [GuardrailEventHooks.pre_call]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> dict:
        keywords = _blocked_keywords()
        if not keywords:
            return data

        messages = data.get("messages")
        if not messages:
            return data

        text = _extract_text(messages)

        for kw in keywords:
            if kw in text:
                logger.warning("keyword_blocked keyword=%r", kw)
                raise ValueError(
                    f"This prompt contains restricted content and has been blocked by Airlock. "
                    f"Please remove any references to restricted terms and try again."
                )

        return data
