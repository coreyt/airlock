"""Unified text extraction seam — handles both LLM and MCP data shapes.

Neutral top-level home (no ``fast``/``guardrails`` imports) so both layers can
depend on it without forming an import cycle. ``airlock.guardrails.extract``
re-exports these names for backward compatibility.

LiteLLM's MCP integration creates synthetic ``messages`` via
``_convert_mcp_to_llm_format()`` and preserves the original tool name and
arguments as ``mcp_tool_name`` and ``mcp_arguments`` in the data dict.

All guardrails should use ``extract_text(data, call_type)`` as the single
entry point so that both LLM completions and MCP tool calls are scanned
through the same pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
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


#: Roles whose content is attacker-controlled input for Phase A classification.
#: System and developer turns are operator-authored instructions, and assistant
#: turns are model output — feeding either to an injection classifier invites
#: false positives on Airlock's own system prompt. Tool/function results are
#: untrusted but are *indirect* input: they belong to Phase B, which needs
#: provenance at the retrieval boundary that does not exist yet.
_DIRECT_INPUT_ROLES = frozenset({"user"})


@dataclass(frozen=True)
class DirectInput:
    """Role-preserving view of the text a classifier should treat as input.

    ``kind`` is ``user_prompt``, ``mcp_arguments``, or ``none``. ``excluded_roles``
    records which roles were present but withheld, so a run can show that
    exclusion was deliberate rather than a parsing miss.
    """

    text: str
    kind: str
    excluded_roles: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.text.strip())


def extract_direct_input(data: dict, call_type: str = "") -> DirectInput:
    """Extract current user-originated text for Phase A injection classification.

    This deliberately differs from :func:`extract_text`, which flattens every
    message into one blob for keyword and PII scanning. Flattening is wrong for
    an injection classifier in two ways: it hands the classifier Airlock's own
    system prompt (which discusses attacks and reads as one), and it re-submits
    conversation history that was already classified on the request that
    introduced it.

    Only the **most recent** user turn is returned for message-shaped requests:
    that is the new, unclassified content in this request. Earlier user turns
    were classified when they arrived.

    Callers must invoke this *after* PII redaction — the PII guard mutates
    ``data["messages"]`` in place, so reading the current messages yields
    redacted text.
    """
    if is_mcp_call(data, call_type):
        arguments = data.get("mcp_arguments")
        if arguments is None:
            return DirectInput("", "none")
        return DirectInput("\n".join(_collect_strings(arguments)), "mcp_arguments")

    messages = data.get("messages")
    if not isinstance(messages, list):
        return DirectInput("", "none")

    excluded: set[str] = set()
    latest: str | None = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", ""))
        if role in _DIRECT_INPUT_ROLES:
            latest = extract_text_from_messages([message])
        elif role:
            excluded.add(role)

    if latest is None:
        return DirectInput("", "none", tuple(sorted(excluded)))
    return DirectInput(latest, "user_prompt", tuple(sorted(excluded)))


_MAX_DEPTH = 20


def _collect_strings(value: Any, _depth: int = 0) -> list[str]:
    """Recursively collect all string representations from a value.

    Handles nested dicts and lists so that keywords and PII buried
    in structured MCP arguments are not invisible to guardrails.
    Stops at _MAX_DEPTH to guard against adversarial payloads.
    """
    if _depth >= _MAX_DEPTH:
        return []
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
            parts.extend(_collect_strings(v, _depth + 1))
    elif isinstance(value, list):
        for item in value:
            parts.extend(_collect_strings(item, _depth + 1))
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


# LiteLLM call_types for batch/file routes. These carry no top-level model and
# no messages, so model-specific guardrail logic must be skipped for them.
_BATCH_CALL_TYPES = frozenset(
    {
        "create_batch",
        "acreate_batch",
        "retrieve_batch",
        "aretrieve_batch",
        "cancel_batch",
        "acancel_batch",
        "create_file",
        "acreate_file",
        "file_content",
        "afile_content",
        "file_retrieve",
        "afile_retrieve",
        "file_delete",
        "afile_delete",
        "file_list",
        "afile_list",
    }
)


def is_batch_call(data: dict, call_type: str = "") -> bool:
    """Return True if this request is a batch/file route.

    ``call_type`` is authoritative: when it is non-empty, the result is solely
    ``call_type in _BATCH_CALL_TYPES`` and caller-controlled data markers are
    ignored (a normal ``completion``/``acompletion`` carrying ``input_file_id``
    or ``purpose == "batch"`` is NOT a batch call, so it cannot bypass the
    guardrails). The data markers are only a fallback consulted when
    ``call_type`` is empty/unset, and even then a completion-shaped payload
    wins: if ``data`` carries any of ``messages``/``prompt``/``input`` it is
    treated as a completion (False); otherwise ``input_file_id`` present, or
    ``purpose == "batch"``, marks it as batch.
    """
    if call_type:
        return call_type in _BATCH_CALL_TYPES
    if "messages" in data or "prompt" in data or "input" in data:
        return False
    if "input_file_id" in data:
        return True
    return data.get("purpose") == "batch"


# Per-request cache key, stored under ``data["metadata"]``. Scoped to a single
# request (a new request is a new ``data`` dict, hence a fresh cache) so there is
# no cross-request leakage. The underscore prefix marks it as Airlock-internal.
_TEXT_CACHE_KEY = "_airlock_text"


def _compute_text(data: dict, call_type: str) -> str:
    if is_mcp_call(data, call_type):
        return extract_text_from_mcp(data)
    return extract_text_from_messages(data.get("messages", []))


def extract_text(data: dict, call_type: str = "") -> str:
    """Dispatch: MCP if call_type == 'call_mcp_tool' or 'mcp_tool_name' in data,
    else LLM messages.

    Cache-aware (per-request, metadata-scoped): if ``data["metadata"]`` already
    holds a computed value it is returned as-is; otherwise the text is computed
    from the *current* messages/arguments and stored so subsequent guards in the
    same request reuse it instead of re-walking. The PII guard runs first and
    refreshes this cache with post-redaction text (see ``refresh_text_cache``),
    so downstream keyword + guardian reads are post-redaction.
    """
    metadata = data.get("metadata")
    if isinstance(metadata, dict) and _TEXT_CACHE_KEY in metadata:
        return metadata[_TEXT_CACHE_KEY]
    text = _compute_text(data, call_type)
    data.setdefault("metadata", {})[_TEXT_CACHE_KEY] = text
    return text


def refresh_text_cache(data: dict, call_type: str = "") -> str:
    """Recompute the request text from the *current* messages/arguments and
    overwrite the per-request cache.

    Called by the PII guard after redaction (it is the only pre-call guard that
    mutates ``data["messages"]``) so downstream guards read post-redaction text.
    Any caller that mutates the messages must invalidate the cache this way.
    """
    text = _compute_text(data, call_type)
    data.setdefault("metadata", {})[_TEXT_CACHE_KEY] = text
    return text
