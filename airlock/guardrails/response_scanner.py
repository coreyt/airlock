"""
Airlock Response Scanner — post-call guardrail that scans LLM responses
and MCP tool results for prompt injection patterns, instruction override
attempts, and data exfiltration markers.

Uses fast regex heuristics (~microseconds). No ML, no external APIs.

Three response paths:
  - Non-streaming LLM: async_post_call_success_hook (guardrail dispatch)
  - Streaming LLM: async_post_call_streaming_iterator_hook (guardrail dispatch)
  - MCP tool results: async_post_mcp_tool_call_hook (success_callback list)

Default mode: observe (log detections, don't block).

Note: the streaming path (async_post_call_streaming_iterator_hook) yields
chunks as they arrive, then scans after the stream completes. It cannot
block retroactively — detection is logged + attached as metadata for the
slow analyzer. This is acceptable because streaming goes to user-facing
agents that process further before acting.

Env vars:
    AIRLOCK_RESPONSE_SCAN_MODE      — observe (default) or enforce
    AIRLOCK_RESPONSE_SCAN_THRESHOLD — composite score threshold (default 0.5)
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass, field
from typing import Any

from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.callbacks.metrics import record_response_scan_detection

logger = logging.getLogger("airlock.guardrails.response_scanner")


# ---------------------------------------------------------------------------
# Scan result
# ---------------------------------------------------------------------------
@dataclass
class ScanResult:
    """Result of scanning text for injection/exfil patterns."""

    detected_categories: list[str] = field(default_factory=list)
    composite_score: float = 0.0
    should_block: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Detection patterns (compiled regex, 4 categories)
# ---------------------------------------------------------------------------
_CATEGORY_WEIGHTS = {
    "injection": 1.0,
    "override": 0.8,
    "exfiltration": 0.9,
    "tool_call": 0.7,
}

_TOTAL_WEIGHT = sum(_CATEGORY_WEIGHTS.values())

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules)", re.IGNORECASE),
    re.compile(r"disregard\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions|rules)", re.IGNORECASE),
    re.compile(r"forget\s+(?:everything|all|your)\s+(?:instructions|you\s+were\s+told)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:new|different|my)", re.IGNORECASE),
    re.compile(r"(?:new|updated|override)\s+(?:system\s+)?instructions\s*:", re.IGNORECASE),
    re.compile(r"DAN\s+mode", re.IGNORECASE),
    re.compile(r"do\s+anything\s+now", re.IGNORECASE),
    re.compile(r"pretend\s+(?:you\s+)?(?:are|to\s+be)\s+(?:an?\s+)?(?:unrestricted|unfiltered)", re.IGNORECASE),
]

_OVERRIDE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\[(?:SYSTEM|INST|/INST|ADMIN)\]", re.IGNORECASE),
    re.compile(r"<\|(?:im_start|im_end|system|endoftext)\|>"),
    re.compile(r"###\s+(?:System|Human|Assistant)\s*:"),
    re.compile(r"(?:begin|start)\s+(?:new\s+)?(?:conversation|session|context)", re.IGNORECASE),
]

_EXFILTRATION_PATTERNS: list[re.Pattern] = [
    # Credential-like: key=<base64 40+ chars>
    re.compile(r"(?:key|token|secret|password)\s*=\s*[A-Za-z0-9+/]{40,}"),
    # URL with sensitive query params
    re.compile(r"https?://[^\s]+\?(?:[^\s]*&)?(?:key|token|secret|password)=[^\s&]{8,}"),
    # Markdown image exfiltration
    re.compile(r"!\[[^\]]*\]\(https?://[^\)]+\?[^\)]*data=[^\)]+\)"),
    # Explicit exfil language
    re.compile(r"(?:send|forward|transmit|exfiltrate)\s+(?:(?:this|the)\s+)?(?:data|conversation|information|content)\s+to", re.IGNORECASE),
]

_TOOL_CALL_PATTERNS: list[re.Pattern] = [
    re.compile(r"<(?:tool_use|function_call|tool_call)>"),
    re.compile(r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:'),
]


def _check_patterns(text: str, patterns: list[re.Pattern]) -> list[str]:
    """Return list of pattern matches found in text."""
    return [m.group() for p in patterns for m in [p.search(text)] if m]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _scan_text(text: str, mode: str = "observe") -> ScanResult:
    """Scan text against all pattern categories and compute composite score."""
    if not text:
        return ScanResult()

    threshold = float(os.getenv("AIRLOCK_RESPONSE_SCAN_THRESHOLD", "0.5"))

    categories: dict[str, list[str]] = {}
    injection_hits = _check_patterns(text, _INJECTION_PATTERNS)
    if injection_hits:
        categories["injection"] = injection_hits

    override_hits = _check_patterns(text, _OVERRIDE_PATTERNS)
    if override_hits:
        categories["override"] = override_hits

    exfil_hits = _check_patterns(text, _EXFILTRATION_PATTERNS)
    if exfil_hits:
        categories["exfiltration"] = exfil_hits

    tool_hits = _check_patterns(text, _TOOL_CALL_PATTERNS)
    if tool_hits:
        categories["tool_call"] = tool_hits

    if not categories:
        return ScanResult()

    detected_weight = sum(_CATEGORY_WEIGHTS[c] for c in categories)
    composite = detected_weight / _TOTAL_WEIGHT

    for cat in categories:
        record_response_scan_detection(cat, mode)

    return ScanResult(
        detected_categories=list(categories.keys()),
        composite_score=round(composite, 4),
        should_block=composite >= threshold,
        details={cat: hits for cat, hits in categories.items()},
    )


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------
def _extract_response_text(response: Any) -> str:
    """Extract text from a ModelResponse (non-streaming)."""
    parts: list[str] = []
    if not response or not hasattr(response, "choices"):
        return ""
    for choice in response.choices:
        msg = getattr(choice, "message", None)
        if not msg:
            continue
        content = getattr(msg, "content", None)
        if content:
            parts.append(content)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                fn = getattr(tc, "function", None)
                if fn:
                    name = getattr(fn, "name", "")
                    args = getattr(fn, "arguments", "")
                    if name:
                        parts.append(name)
                    if args:
                        parts.append(args)
    return "\n".join(parts)


def _extract_mcp_response_text(response_obj: Any) -> str:
    """Extract text from an MCP tool call response."""
    if not response_obj:
        return ""
    # response_obj may have mcp_tool_call_response list
    items = getattr(response_obj, "mcp_tool_call_response", None)
    if not items:
        return ""
    parts: list[str] = []
    for item in items:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Guardrail class
# ---------------------------------------------------------------------------
class AirlockResponseScanner(CustomGuardrail):
    """Post-call guardrail that scans responses for injection/exfil patterns."""

    def __init__(self, **kwargs):
        supported_event_hooks = [GuardrailEventHooks.post_call]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    # Path 1 — Non-streaming LLM
    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: Any,  # noqa: ARG002
        response: Any,
    ) -> Any:
        text = _extract_response_text(response)
        if not text:
            return response

        mode = _mode()
        result = _scan_text(text, mode)
        if not result.detected_categories:
            return response

        _attach_metadata(data, result)

        logger.warning(
            "response_scan_detected categories=%s score=%.4f model=%s",
            result.detected_categories,
            result.composite_score,
            data.get("model", "unknown"),
        )

        if result.should_block and mode == "enforce":
            raise ValueError(
                "Response blocked: potential injection content detected"
            )

        return response

    # Path 2 — Streaming LLM (buffer-and-forward)
    # Must be defined directly on the class (LiteLLM checks type.__dict__)
    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,  # noqa: ARG002
        response: Any,
        request_data: dict,
    ) -> AsyncGenerator:
        # Accumulate only text content, not full chunk objects
        text_parts: list[str] = []
        async for chunk in response:
            yield chunk
            for choice in getattr(chunk, "choices", []):
                delta = getattr(choice, "delta", None)
                if delta:
                    content = getattr(delta, "content", None)
                    if content:
                        text_parts.append(content)

        # Scan accumulated text after stream completes
        full_text = "".join(text_parts)
        if full_text:
            mode = _mode()
            result = _scan_text(full_text, mode)
            _attach_metadata(request_data, result)
            if result.detected_categories:
                logger.warning(
                    "response_scan_detected_in_stream categories=%s score=%.4f mode=%s",
                    result.detected_categories,
                    result.composite_score,
                    mode,
                )

    # Path 3 — MCP tool results (via success_callback registration)
    async def async_post_mcp_tool_call_hook(
        self,
        kwargs: dict,
        response_obj: Any,
        start_time: Any,  # noqa: ARG002
        end_time: Any,  # noqa: ARG002
    ) -> None:
        text = _extract_mcp_response_text(response_obj)
        if not text:
            return None

        mode = _mode()
        result = _scan_text(text, mode)

        # Attach to kwargs metadata for enterprise logger
        metadata = kwargs.setdefault("litellm_params", {}).setdefault(
            "metadata", {}
        )
        metadata["airlock_response_scan"] = result.to_dict()

        if result.detected_categories:
            logger.warning(
                "response_scan_mcp_detected tool=%s categories=%s score=%.4f",
                kwargs.get("mcp_tool_name", "unknown"),
                result.detected_categories,
                result.composite_score,
            )

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mode() -> str:
    return os.getenv("AIRLOCK_RESPONSE_SCAN_MODE", "observe")


def _attach_metadata(data: dict, result: ScanResult) -> None:
    data.setdefault("metadata", {})["airlock_response_scan"] = result.to_dict()


# ---------------------------------------------------------------------------
# Module-level instance + self-registration (for MCP path)
# ---------------------------------------------------------------------------
response_scanner = AirlockResponseScanner()


def _self_register() -> None:
    """Register into success callback lists for MCP post-call hook."""
    try:
        import litellm

        mgr = litellm.logging_callback_manager
        mgr.add_litellm_success_callback(response_scanner)
        mgr.add_litellm_async_success_callback(response_scanner)
    except Exception:
        logger.debug("response_scanner self-registration deferred", exc_info=True)


_self_register()
