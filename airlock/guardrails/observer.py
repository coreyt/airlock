"""
Airlock Observer — zero-cost during_call guardrail for signal collection.

Runs in parallel with the LLM API call via asyncio.gather, adding zero
latency to the request path.  Scans each request for PII patterns,
blocked keywords, and threat score, then attaches structured
GuardrailObservation to metadata for downstream logging.

Critical design constraints:
  - PII scan uses lightweight regex, NOT Presidio (too heavy for during_call)
  - Keyword scan uses the same substring logic as keyword_guard, no raise
  - Threat read reads client.threat_score from StateStore — does NOT call
    assess_threat() (guardian already called it in pre_call)
  - NEVER raises — observation only
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict
from typing import Any

from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.callbacks.metrics import (
    record_keyword_block,
    record_pii_redaction,
    record_threat_block,
)
from airlock.fast.state import store

from .schemas import GuardrailObservation, GuardrailSignal

logger = logging.getLogger("airlock.guardrails.observer")

# ---------------------------------------------------------------------------
# Lightweight PII regex patterns (NOT Presidio — too heavy for during_call)
# ---------------------------------------------------------------------------
_PII_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL_ADDRESS": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "PHONE_NUMBER": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "US_SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
}


# ---------------------------------------------------------------------------
# Text extraction (shared with orchestrator/enforcer)
# ---------------------------------------------------------------------------
def extract_text(messages: list[dict[str, Any]]) -> str:
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
    return "\n".join(parts)


def _blocked_keywords() -> list[str]:
    raw = os.getenv("AIRLOCK_BLOCKED_KEYWORDS", "")
    return [kw.strip().lower() for kw in raw.split(",") if kw.strip()]


# ---------------------------------------------------------------------------
# Signal scanners
# ---------------------------------------------------------------------------
def scan_pii(text: str) -> GuardrailSignal:
    """Lightweight regex PII scan — counts entity types present."""
    start = time.monotonic()
    found: dict[str, int] = {}
    for entity_type, pattern in _PII_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            found[entity_type] = len(matches)
            for _ in matches:
                record_pii_redaction(entity_type)

    total = sum(found.values())
    elapsed = (time.monotonic() - start) * 1000
    return GuardrailSignal(
        guardrail_name="pii_scan",
        detected=total > 0,
        score=min(1.0, total / 5.0),  # 5+ entities → score 1.0
        details={"entities": found, "total_count": total},
        duration_ms=round(elapsed, 2),
    )


def scan_keywords(text: str) -> GuardrailSignal:
    """Keyword substring scan — returns signal instead of raising."""
    start = time.monotonic()
    keywords = _blocked_keywords()
    text_lower = text.lower()
    matched = [kw for kw in keywords if kw in text_lower]

    if matched:
        record_keyword_block()

    elapsed = (time.monotonic() - start) * 1000
    return GuardrailSignal(
        guardrail_name="keyword_scan",
        detected=len(matched) > 0,
        score=1.0 if matched else 0.0,  # binary: any keyword match → 1.0
        details={"matched_keywords": matched, "match_count": len(matched)},
        duration_ms=round(elapsed, 2),
    )


def read_threat(user_api_key_dict: Any) -> GuardrailSignal:
    """Read the current threat score from StateStore — does NOT recompute."""
    start = time.monotonic()
    client_id = _extract_client_id(user_api_key_dict)
    client = store.get_client(client_id)
    threat_score = client.threat_score

    if threat_score >= 0.8:
        record_threat_block()

    elapsed = (time.monotonic() - start) * 1000
    return GuardrailSignal(
        guardrail_name="threat_read",
        detected=threat_score >= 0.8,
        score=threat_score,
        details={
            "client_id": client_id,
            "threat_score": threat_score,
            "in_backoff": client.is_in_backoff(),
        },
        duration_ms=round(elapsed, 2),
    )


def _extract_client_id(user_api_key_dict: Any) -> str:
    """Derive a stable client identifier from the API-key metadata."""
    if user_api_key_dict:
        if hasattr(user_api_key_dict, "api_key"):
            key = user_api_key_dict.api_key or ""
            if len(key) > 8:
                return f"key:{key[-8:]}"
        if isinstance(user_api_key_dict, dict):
            return f"key:{user_api_key_dict.get('api_key', 'unknown')[-8:]}"
    return "unknown"


def collect_signals(
    data: dict, user_api_key_dict: Any
) -> list[GuardrailSignal]:
    """Run all signal scanners and return a list of signals."""
    text = extract_text(data.get("messages", []))
    return [
        scan_pii(text),
        scan_keywords(text),
        read_threat(user_api_key_dict),
    ]


# ---------------------------------------------------------------------------
# LiteLLM during_call guardrail
# ---------------------------------------------------------------------------
class AirlockObserver(CustomGuardrail):
    """During-call guardrail that observes and records without blocking."""

    def __init__(self, **kwargs):
        supported_event_hooks = [GuardrailEventHooks.during_call]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    async def async_moderation_hook(
        self,
        data: dict,
        user_api_key_dict: Any,
        call_type: str,
    ) -> None:
        try:
            signals = collect_signals(data, user_api_key_dict)
            client_id = _extract_client_id(user_api_key_dict)

            observation = GuardrailObservation(
                request_id=data.get("litellm_call_id"),
                model=data.get("model", "unknown"),
                client_id=client_id,
                signals=signals,
            )
            data.setdefault("metadata", {})["airlock_observation"] = asdict(
                observation
            )

            detected = [s for s in signals if s.detected]
            if detected:
                logger.info(
                    "observation_recorded detected=%s model=%s client=%s",
                    [s.guardrail_name for s in detected],
                    observation.model,
                    client_id,
                )
        except Exception:
            # Observer must never fail the request
            logger.exception("observer_error — swallowed")
