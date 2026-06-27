"""Backward-compatible re-export shim for the text-extraction seam.

The real implementations live in :mod:`airlock.text_extract` (a neutral
top-level module with no ``fast``/``guardrails`` imports) so that both layers
can depend on the seam without forming an import cycle. Existing importers of
``airlock.guardrails.extract`` keep working unchanged.
"""

from __future__ import annotations

from airlock.text_extract import (  # noqa: F401
    _BATCH_CALL_TYPES,
    _collect_strings,
    _MAX_DEPTH,
    extract_text,
    extract_text_from_mcp,
    extract_text_from_messages,
    is_batch_call,
    is_mcp_call,
)

__all__ = [
    "extract_text",
    "extract_text_from_mcp",
    "extract_text_from_messages",
    "is_batch_call",
    "is_mcp_call",
]
