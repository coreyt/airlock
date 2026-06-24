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
import re
import unicodedata
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from . import _env_flag
from .extract import extract_text

logger = logging.getLogger("airlock.guardrails.keyword")


# Zero-width characters that can be used to bypass keyword matching
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def _normalize_text(text: str) -> str:
    """Normalize Unicode text for robust keyword matching.

    Applies NFKD normalization (decomposes fullwidth/compatibility chars),
    strips zero-width characters, and normalizes whitespace variants
    (non-breaking spaces, etc.) to regular spaces.
    """
    text = unicodedata.normalize("NFKD", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    # Normalize various Unicode space characters to ASCII space
    text = re.sub(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]", " ", text)
    return text


_cached_raw: str | None = None
_cached_keywords: list[str] = []


def _blocked_keywords() -> list[str]:
    global _cached_raw, _cached_keywords
    raw = os.getenv("AIRLOCK_BLOCKED_KEYWORDS", "")
    if raw != _cached_raw:
        _cached_raw = raw
        _cached_keywords = [
            _normalize_text(kw.strip()).lower() for kw in raw.split(",") if kw.strip()
        ]
    return _cached_keywords


class AirlockKeywordGuard(CustomGuardrail):
    """Pre-call guardrail that rejects prompts containing blocked keywords."""

    def __init__(self, **kwargs):
        supported_event_hooks = [
            GuardrailEventHooks.pre_call,
            GuardrailEventHooks.pre_mcp_call,
        ]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> dict:
        if not _env_flag("AIRLOCK_KW_ENABLED"):
            return data

        # Honour a per-request capability skip (CC-10): "off" skips entirely,
        # "observe" still scans + logs but does not block.
        from airlock.guardrails.overrides import effective_mode

        mode = effective_mode(data, "keyword")
        if mode == "off":
            return data

        keywords = _blocked_keywords()
        if not keywords:
            return data

        text = _normalize_text(extract_text(data, call_type)).lower()
        if not text:
            return data

        for kw in keywords:
            if kw in text:
                logger.warning("keyword_blocked keyword=%r mode=%s", kw, mode)
                if mode == "enforce":
                    raise ValueError(
                        "This prompt contains restricted content and has been blocked by Airlock. "
                        "Please remove any references to restricted terms and try again."
                    )
                return data  # observe: logged but not blocked

        return data
