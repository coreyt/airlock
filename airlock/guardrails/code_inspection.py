"""Observational inspection of generated programmatic-tool code.

The result intentionally contains categories and counts, never matched source
text.  This makes it safe to persist alongside the canonical request record.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .observer import _PII_PATTERNS, _blocked_keywords

_CODE_BLOCK = re.compile(r"```(?:[\w+-]+)?\n(.*?)```", re.DOTALL)
_RESTRICTED_TOOL = re.compile(
    r"\b(?:subprocess|os\.system|eval|exec|pickle\.loads)\b", re.I
)
_RESOURCE_ACCESS = re.compile(
    r"\b(?:open\s*\(|Path\s*\(|requests\.|urllib\.|socket\.)", re.I
)


def inspection_enabled() -> bool:
    return os.getenv("AIRLOCK_INSPECT_CODE", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def inspect_code(text: str) -> dict[str, Any]:
    """Return safe category/count evidence for fenced code in *text*."""
    blocks = _CODE_BLOCK.findall(text or "")
    findings: dict[str, int] = {}
    source = "\n".join(blocks)
    if not source or not inspection_enabled():
        return {
            "enabled": inspection_enabled(),
            "code_blocks": len(blocks),
            "findings": findings,
            "score": 0.0,
        }
    for name, pattern in _PII_PATTERNS.items():
        count = len(pattern.findall(source))
        if count:
            findings[f"pii:{name}"] = count
    lower = source.lower()
    for keyword in _blocked_keywords():
        count = lower.count(keyword)
        if count:
            findings["blocked_keyword"] = findings.get("blocked_keyword", 0) + count
    for category, pattern in (
        ("restricted_tool", _RESTRICTED_TOOL),
        ("resource_access", _RESOURCE_ACCESS),
    ):
        count = len(pattern.findall(source))
        if count:
            findings[category] = count
    return {
        "enabled": True,
        "code_blocks": len(blocks),
        "findings": findings,
        "score": min(1.0, sum(findings.values()) / 5.0),
        "enforcement_weight": 0.0,
    }
