"""
Airlock PII Guard — strips personally identifiable information from prompts
before they leave the corporate network, and restores original values in
tool-call arguments on the response path.

Uses Microsoft Presidio for entity detection and anonymization.

Two-phase pipeline:
  - Pre-call: redact PII with numbered placeholders, store reverse mapping.
  - Post-call: hydrate tool-call arguments using that mapping.

Streaming hydration is deferred — tool-call deltas may split placeholders
across chunks, requiring buffering and reassembly.  The non-streaming path
covers the primary client (Claude Code).
See dev/design-note-pii-rehydration.md §7 and dev/impl-plan-pii-rehydration.md
Phase 5 for the deferred streaming approach.

Env vars:
    AIRLOCK_PII_ENTITIES   — comma-separated entity types to redact
                             (default: CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER)
    AIRLOCK_PII_HYDRATION  — 'tools' (default) or 'off'
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from . import _env_flag
from .extract import is_mcp_call

logger = logging.getLogger("airlock.guardrails.pii")

DEFAULT_ENTITIES = "CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER"

# Lazy-loaded so the import doesn't fail at module level if presidio
# isn't installed (allows the rest of Airlock to still work).
_analyzer = None
_anonymizer = None
_presidio_lock = threading.Lock()


def _get_presidio():
    global _analyzer, _anonymizer
    if _analyzer is None:
        with _presidio_lock:
            if _analyzer is None:  # re-check inside the lock
                from presidio_analyzer import AnalyzerEngine
                from presidio_anonymizer import AnonymizerEngine

                analyzer = AnalyzerEngine()
                anonymizer = AnonymizerEngine()
                _analyzer = analyzer
                _anonymizer = anonymizer
    return _analyzer, _anonymizer


def _configured_entities() -> list[str]:
    raw = os.getenv("AIRLOCK_PII_ENTITIES", DEFAULT_ENTITIES)
    return [e.strip() for e in raw.split(",") if e.strip()]


# ---------------------------------------------------------------------------
# Core scrubbing with numbered placeholders and reverse mapping
# ---------------------------------------------------------------------------
def _scrub_text_with_mapping(
    text: str,
    mapping: dict[str, str],
    counters: dict[str, int],
) -> str:
    """Redact PII with numbered placeholders and record the reverse mapping.

    *mapping* and *counters* are mutated in place so a single request
    accumulates a consistent placeholder namespace across all messages and
    MCP arguments.
    """
    analyzer, _ = _get_presidio()
    entities = _configured_entities()
    results = analyzer.analyze(text=text, entities=entities, language="en")
    if not results:
        return text

    # Sort by start offset descending so replacements don't shift positions.
    results.sort(key=lambda r: r.start, reverse=True)

    for result in results:
        original = text[result.start : result.end]

        # Dedup: reuse placeholder if this exact value was already seen.
        existing = next(
            (ph for ph, orig in mapping.items() if orig == original),
            None,
        )
        if existing:
            placeholder = existing
        else:
            entity_type = result.entity_type
            counters[entity_type] = counters.get(entity_type, 0) + 1
            placeholder = f"<{entity_type}_{counters[entity_type]}>"
            mapping[placeholder] = original

        text = text[: result.start] + placeholder + text[result.end :]

    return text


def _scrub_text(text: str) -> str:
    """Convenience wrapper — scrub without tracking the mapping."""
    return _scrub_text_with_mapping(text, {}, {})


def _scrub_messages(
    messages: list[dict[str, Any]],
    mapping: dict[str, str] | None = None,
    counters: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Scrub PII from each message's content field.

    When *mapping*/*counters* are provided, numbered placeholders are used
    and the reverse mapping is accumulated.  When omitted, throwaway dicts
    are created (no mapping captured).
    """
    if mapping is None:
        mapping = {}
    if counters is None:
        counters = {}
    cleaned = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            msg = {
                **msg,
                "content": _scrub_text_with_mapping(content, mapping, counters),
            }
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    new_parts.append(
                        {
                            **part,
                            "text": _scrub_text_with_mapping(
                                part.get("text", ""), mapping, counters
                            ),
                        }
                    )
                else:
                    new_parts.append(part)
            msg = {**msg, "content": new_parts}
        cleaned.append(msg)
    return cleaned


class AirlockPIIGuard(CustomGuardrail):
    """Pre-call PII redaction with post-call hydration of tool-call arguments.

    Streaming hydration (async_post_call_streaming_iterator_hook) is not yet
    implemented.  Tool-call deltas arrive across multiple chunks and a
    placeholder token may span a chunk boundary, so hydration requires
    accumulating the full function.arguments string before replacing.
    See dev/design-note-pii-rehydration.md §7 for the deferred approach.
    """

    def __init__(self, **kwargs):
        # NOTE: post_call covers the non-streaming response path only.
        # A future async_post_call_streaming_iterator_hook would need to
        # buffer tool-call argument deltas and hydrate after assembly.
        # See dev/impl-plan-pii-rehydration.md Phase 5.
        supported_event_hooks = [
            GuardrailEventHooks.pre_call,
            GuardrailEventHooks.pre_mcp_call,
            GuardrailEventHooks.post_call,
        ]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> dict:
        if not _env_flag("AIRLOCK_PII_ENABLED"):
            return data
        mapping: dict[str, str] = {}
        counters: dict[str, int] = {}

        if is_mcp_call(data, call_type):
            _scrub_mcp_arguments(data, mapping, counters)

        messages = data.get("messages")
        if messages:
            data["messages"] = _scrub_messages(messages, mapping, counters)

        if mapping:
            data.setdefault("metadata", {})["airlock_pii_map"] = mapping
            logger.info(
                "pii_redacted count=%d entity_types=%s",
                len(mapping),
                list({k.rsplit("_", 1)[0].strip("<>") for k in mapping}),
            )

            # Warn when streaming is active: PII placeholders in streamed
            # responses will NOT be hydrated back to original values.
            # See dev/design-note-pii-rehydration.md §7.
            if data.get("stream"):
                logger.warning(
                    "pii_streaming_limitation: Streaming is enabled with PII "
                    "redaction active. PII placeholders in streamed responses "
                    "will NOT be hydrated. Tool-call arguments may contain "
                    "placeholders like <EMAIL_ADDRESS_1> instead of real values."
                )

        return data

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: Any,  # noqa: ARG002
        response: Any,
    ) -> Any:
        mapping = data.get("metadata", {}).get("airlock_pii_map")
        if not mapping or not _hydration_enabled():
            return response

        count = _hydrate_tool_calls(response, mapping)
        if count:
            logger.info("pii_hydrated count=%d", count)

        return response


_VALID_HYDRATION_MODES = {"tools", "off"}


def _hydration_enabled() -> bool:
    """Return True unless AIRLOCK_PII_HYDRATION is explicitly 'off'."""
    raw = os.getenv("AIRLOCK_PII_HYDRATION", "tools").strip().lower()
    if raw not in _VALID_HYDRATION_MODES:
        logger.warning("Invalid AIRLOCK_PII_HYDRATION=%r, falling back to 'tools'", raw)
        return True
    return raw != "off"


# ---------------------------------------------------------------------------
# Post-call hydration: restore PII placeholders in tool-call arguments
#
# Handles non-streaming ModelResponse objects only.  For streaming, tool-call
# arguments arrive as incremental deltas across chunks — a placeholder like
# <EMAIL_ADDRESS_1> may be split across two or more deltas.  Hydrating
# individual deltas is unreliable; the streaming path would need to
# accumulate function.arguments deltas, hydrate the assembled JSON, then
# emit a corrective final chunk.
# See dev/design-note-pii-rehydration.md §7 and dev/impl-plan-pii-rehydration.md
# Phase 5.
# ---------------------------------------------------------------------------
def _hydrate_tool_calls(response: Any, mapping: dict[str, str]) -> int:
    """Replace PII placeholders in tool-call arguments. Returns count."""
    count = 0
    if not response or not hasattr(response, "choices"):
        return 0
    for choice in response.choices:
        msg = getattr(choice, "message", None)
        if not msg:
            continue
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            if not fn:
                continue
            args_str = getattr(fn, "arguments", None)
            if not args_str:
                continue
            try:
                args = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                logger.warning("pii_hydration_skip reason=malformed_json")
                continue
            args, n = _hydrate_value_recursive(args, mapping)
            if n:
                count += n
                fn.arguments = json.dumps(args)
    return count


def _hydrate_value_recursive(
    value: Any,
    mapping: dict[str, str],
    _depth: int = 0,
) -> tuple[Any, int]:
    """Replace known placeholders in a JSON-decoded value. Returns (value, count)."""
    if _depth >= 20:
        return value, 0
    if isinstance(value, str):
        count = 0
        for placeholder, original in mapping.items():
            if placeholder in value:
                value = value.replace(placeholder, original)
                count += 1
        return value, count
    elif isinstance(value, dict):
        total = 0
        for k, v in value.items():
            value[k], n = _hydrate_value_recursive(v, mapping, _depth + 1)
            total += n
        return value, total
    elif isinstance(value, list):
        total = 0
        for i, item in enumerate(value):
            value[i], n = _hydrate_value_recursive(item, mapping, _depth + 1)
            total += n
        return value, total
    return value, 0


def _scrub_mcp_arguments(
    data: dict,
    mapping: dict[str, str] | None = None,
    counters: dict[str, int] | None = None,
) -> None:
    """Scrub PII from MCP tool call argument values in place.

    Recurses into nested dicts and lists so PII in structured
    arguments (e.g. {"config": {"email": "user@example.com"}}) is caught.
    """
    if mapping is None:
        mapping = {}
    if counters is None:
        counters = {}
    args = data.get("mcp_arguments")
    if args is not None:
        data["mcp_arguments"] = _scrub_value_recursive(args, mapping, counters)


def _scrub_value_recursive(
    value: Any,
    mapping: dict[str, str],
    counters: dict[str, int],
    _depth: int = 0,
) -> Any:
    """Recursively scrub PII from a value, modifying dicts/lists in place."""
    if _depth >= 20:
        return value
    if isinstance(value, str):
        return _scrub_text_with_mapping(value, mapping, counters)
    elif isinstance(value, dict):
        for k, v in value.items():
            value[k] = _scrub_value_recursive(v, mapping, counters, _depth + 1)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            value[i] = _scrub_value_recursive(item, mapping, counters, _depth + 1)
    return value
