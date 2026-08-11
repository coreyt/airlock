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

import asyncio
import copy
import json
import logging
import os
import threading
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.text_extract import refresh_text_cache
from airlock.transparency import record_redaction

from . import _env_flag
from .extract import is_mcp_call
from .pii_egress import decide as decide_egress
from .pii_egress import egress_mode
from .pii_mapping import PIIMapStore

logger = logging.getLogger("airlock.guardrails.pii")

DEFAULT_ENTITIES = "CREDIT_CARD,US_SSN,EMAIL_ADDRESS,PHONE_NUMBER"
# The shipped environment also enables US_BANK_NUMBER and IBAN_CODE. All six
# are Presidio self-contained pattern/validation recognizers; none needs spaCy
# named-entity inference.
_SELF_CONTAINED_ENTITIES = frozenset(
    (*DEFAULT_ENTITIES.split(","), "US_BANK_NUMBER", "IBAN_CODE")
)

# Lazy-loaded so the import doesn't fail at module level if presidio
# isn't installed (allows the rest of Airlock to still work).
_analyzer = None
_anonymizer = None
_presidio_lock = threading.Lock()


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %d", name, default)
        return default


def _positive_float_env(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %.1f", name, default)
        return default


# One process-local store: the map is never metadata and is consumed by the
# post-call path. Expiry bounds failure/disconnect paths that never reach it.
_pii_map_store = PIIMapStore(
    max_entries=_positive_int_env("AIRLOCK_PII_MAP_MAX_ENTRIES", 1024),
    ttl_seconds=_positive_float_env("AIRLOCK_PII_MAP_TTL_SECONDS", 300.0),
)


def _requires_nlp_entities(entities: list[str]) -> bool:
    """Whether the configured recognizers need spaCy NLP artifacts.

    Airlock's shipped recognizers are self-contained patterns.  Presidio's
    default ``AnalyzerEngine`` would otherwise eagerly load and run spaCy / Thinc
    on their behalf; non-default entities such as PERSON retain that full path.
    """
    return not set(entities).issubset(_SELF_CONTAINED_ENTITIES)


def _create_analyzer():
    """Create the least heavyweight Presidio engine preserving requested PII."""
    from presidio_analyzer import AnalyzerEngine

    entities = _configured_entities()
    if _requires_nlp_entities(entities):
        return AnalyzerEngine()

    # Presidio's supported engine for self-contained recognizers avoids the
    # spaCy / Thinc matrix allocations implicated in the G-9 memory growth.
    from presidio_analyzer.nlp_engine import NoOpNlpEngine

    return AnalyzerEngine(
        nlp_engine=NoOpNlpEngine(
            models=[{"lang_code": "en", "model_name": "airlock-noop"}]
        )
    )


def _get_presidio():
    global _analyzer, _anonymizer
    if _analyzer is None:
        with _presidio_lock:
            if _analyzer is None:  # re-check inside the lock
                from presidio_anonymizer import AnonymizerEngine

                analyzer = _create_analyzer()
                anonymizer = AnonymizerEngine()
                _analyzer = analyzer
                _anonymizer = anonymizer
    return _analyzer, _anonymizer


def _configured_entities() -> list[str]:
    raw = os.getenv("AIRLOCK_PII_ENTITIES", DEFAULT_ENTITIES)
    return [e.strip() for e in raw.split(",") if e.strip()]


def _pii_fail_mode() -> str:
    raw = os.getenv("AIRLOCK_PII_FAIL_MODE", "open").strip().lower()
    if raw not in {"open", "closed"}:
        logger.warning("Invalid AIRLOCK_PII_FAIL_MODE=%r; using 'open'", raw)
        return "open"
    return raw


def _handle_pii_unavailable(data: dict, exc: Exception) -> dict:
    """Apply the explicit redaction-unavailable policy without logging values."""
    mode = _pii_fail_mode()
    data.setdefault("metadata", {})["airlock_pii_unavailable"] = {
        "mode": mode,
        "stage": "pre_call",
        "reason": type(exc).__name__,
    }
    logger.warning("pii_unavailable mode=%s reason=%s", mode, type(exc).__name__)
    if mode == "closed":
        raise ValueError("PII redaction is unavailable; request blocked by policy") from exc
    return data


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
        redacted_messages = None
        redacted_mcp_arguments = None
        mcp_redaction_count = 0
        message_redaction_count = 0
        try:
            if is_mcp_call(data, call_type) and data.get("mcp_arguments") is not None:
                # Scrub a copy. If Presidio fails, fail-open must leave the
                # request byte-identical rather than committing a partial tree.
                redacted_mcp_arguments = copy.deepcopy(data["mcp_arguments"])
                working_data = {"mcp_arguments": redacted_mcp_arguments}
                await asyncio.to_thread(
                    _scrub_mcp_arguments, working_data, mapping, counters
                )
                redacted_mcp_arguments = working_data["mcp_arguments"]
                mcp_redaction_count = len(mapping)

            messages = data.get("messages")
            if messages:
                redacted_messages = await asyncio.to_thread(
                    _scrub_messages, messages, mapping, counters
                )
                message_redaction_count = len(mapping) - mcp_redaction_count
        except Exception as exc:
            return _handle_pii_unavailable(data, exc)

        handle = None
        if mapping and not data.get("stream"):
            handle = _pii_map_store.put(mapping)
            if handle is None:
                return _handle_pii_unavailable(
                    data, RuntimeError("PII reverse-map store is saturated")
                )

        if redacted_mcp_arguments is not None:
            data["mcp_arguments"] = redacted_mcp_arguments
            if mcp_redaction_count:
                record_redaction(
                    data.setdefault("metadata", {}),
                    field="mcp_arguments",
                    count=mcp_redaction_count,
                    category="pii",
                    stage="pre_call",
                    source="pii_guard.mcp",
                )
        if redacted_messages is not None:
            data["messages"] = redacted_messages
            if message_redaction_count:
                record_redaction(
                    data.setdefault("metadata", {}),
                    field="messages",
                    count=message_redaction_count,
                    category="pii",
                    stage="pre_call",
                    source="pii_guard",
                )

        if mapping:
            # Streaming responses cannot safely rehydrate split tool deltas, so
            # do not retain a map that cannot be consumed.
            if handle is not None:
                data.setdefault("metadata", {})["airlock_pii_handle"] = handle
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

        # PII runs first (config.yaml guard order) and is the only pre-call guard
        # that mutates messages. Refresh the shared per-request text cache with the
        # post-redaction text so downstream keyword + guardian reuse it (single
        # extraction) and never see the raw PII.
        refresh_text_cache(data, call_type)

        return data

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: Any,  # noqa: ARG002
        response: Any,
    ) -> Any:
        # Batch/file routes (/v1/batches, /v1/files) invoke this hook with no
        # chat `data` (data is None / has no metadata) — nothing to hydrate.
        metadata = (data or {}).get("metadata") if isinstance(data, dict) else None
        handle = (
            metadata.pop("airlock_pii_handle", None)
            if isinstance(metadata, dict)
            else None
        )
        if not isinstance(handle, str):
            return response
        mapping = _pii_map_store.take(handle)
        if not mapping or not _hydration_enabled():
            return response

        # Telemetry owns the original response object. Hydrate only a private
        # client return value so callback timing cannot write cleartext to a sink.
        try:
            hydrated_response = copy.deepcopy(response)
        except Exception:
            logger.warning("pii_hydration_skip reason=response_copy_failed")
            return response
        mode = egress_mode()
        decisions: list[dict[str, str | bool]] = []
        count = _hydrate_tool_calls(
            hydrated_response, mapping, mode=mode, decisions=decisions
        )
        if isinstance(metadata, dict):
            denied = sum(1 for item in decisions if not item["allow"])
            metadata["airlock_pii_egress"] = {
                "mode": mode,
                "hydrated": count,
                "would_suppress": denied,
                # Tool/path/class/reason are value-free policy telemetry. Cap the
                # event so a malicious response cannot create an oversized log.
                "decisions": decisions[:64],
                "truncated": len(decisions) > 64,
            }
        if count:
            logger.info("pii_hydrated count=%d", count)

        return hydrated_response


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
def _hydrate_tool_calls(
    response: Any,
    mapping: dict[str, str],
    *,
    mode: str = "observe",
    decisions: list[dict[str, str | bool]] | None = None,
) -> int:
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
            tool = str(getattr(fn, "name", "unknown") or "unknown")
            args, n = _hydrate_value_recursive(
                args,
                mapping,
                tool=tool,
                mode=mode,
                decisions=decisions,
            )
            if n:
                count += n
                fn.arguments = json.dumps(args)
    return count


def _hydrate_value_recursive(
    value: Any,
    mapping: dict[str, str],
    _depth: int = 0,
    *,
    tool: str = "unknown",
    path: str = "",
    mode: str = "observe",
    decisions: list[dict[str, str | bool]] | None = None,
) -> tuple[Any, int]:
    """Replace known placeholders in a JSON-decoded value. Returns (value, count)."""
    if _depth >= 20:
        return value, 0
    if isinstance(value, str):
        count = 0
        for placeholder, original in mapping.items():
            if placeholder in value:
                decision = decide_egress(
                    tool=tool, path=path or "/", placeholder=placeholder
                )
                if decisions is not None:
                    decisions.append(
                        {
                            "allow": decision.allow,
                            "reason": decision.reason,
                            "entity_type": decision.entity_type,
                            "tool": decision.tool,
                            "path": decision.path,
                        }
                    )
                if decision.allow or mode == "observe":
                    value = value.replace(placeholder, original)
                    count += 1
        return value, count
    elif isinstance(value, dict):
        total = 0
        for k, v in value.items():
            value[k], n = _hydrate_value_recursive(
                v,
                mapping,
                _depth + 1,
                tool=tool,
                path=f"{path}/{str(k).replace('~', '~0').replace('/', '~1')}",
                mode=mode,
                decisions=decisions,
            )
            total += n
        return value, total
    elif isinstance(value, list):
        total = 0
        for i, item in enumerate(value):
            value[i], n = _hydrate_value_recursive(
                item,
                mapping,
                _depth + 1,
                tool=tool,
                path=f"{path}/{i}",
                mode=mode,
                decisions=decisions,
            )
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
