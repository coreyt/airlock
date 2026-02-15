"""
Airlock PII Guard — strips personally identifiable information from prompts
before they leave the corporate network.

Uses Microsoft Presidio for entity detection and anonymization.

Env vars:
    AIRLOCK_PII_ENTITIES — comma-separated list of entity types to redact
        (default: CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

logger = logging.getLogger("airlock.guardrails.pii")

DEFAULT_ENTITIES = "CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER"

# Lazy-loaded so the import doesn't fail at module level if presidio
# isn't installed (allows the rest of Airlock to still work).
_analyzer = None
_anonymizer = None


def _get_presidio():
    global _analyzer, _anonymizer
    if _analyzer is None:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine

        _analyzer = AnalyzerEngine()
        _anonymizer = AnonymizerEngine()
    return _analyzer, _anonymizer


def _configured_entities() -> list[str]:
    raw = os.getenv("AIRLOCK_PII_ENTITIES", DEFAULT_ENTITIES)
    return [e.strip() for e in raw.split(",") if e.strip()]


def _scrub_text(text: str) -> str:
    """Run Presidio on a single string, returning the anonymized version."""
    analyzer, anonymizer = _get_presidio()
    entities = _configured_entities()
    results = analyzer.analyze(text=text, entities=entities, language="en")
    if not results:
        return text
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results)
    logger.info("pii_redacted count=%d entities=%s", len(results), [r.entity_type for r in results])
    return anonymized.text


def _scrub_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scrub PII from each message's content field."""
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            msg = {**msg, "content": _scrub_text(content)}
        elif isinstance(content, list):
            # Handle multi-part messages (text + images)
            new_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    new_parts.append({**part, "text": _scrub_text(part.get("text", ""))})
                else:
                    new_parts.append(part)
            msg = {**msg, "content": new_parts}
        cleaned.append(msg)
    return cleaned


class AirlockPIIGuard(CustomGuardrail):
    """Pre-call guardrail that strips PII from outbound prompts."""

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
        messages = data.get("messages")
        if messages:
            data["messages"] = _scrub_messages(messages)
        return data
