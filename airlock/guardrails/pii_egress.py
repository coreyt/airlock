"""Value-free policy decisions for PII rehydration into tool-call arguments."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("airlock.guardrails.pii")

_VALID_MODES = frozenset({"observe", "shadow", "enforce"})
_VALID_BANDS = frozenset({"round_trip", "exfil"})
_DEFAULT_BLOCKED_CLASSES = frozenset(
    {"CREDIT_CARD", "US_SSN", "US_BANK_NUMBER", "IBAN_CODE"}
)


@dataclass(frozen=True)
class EgressDecision:
    allow: bool
    reason: str
    entity_type: str
    tool: str
    path: str


def _json_list_env(name: str) -> list[dict[str, Any]]:
    raw = os.getenv(name, "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid %s JSON; treating it as an empty list", name)
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        logger.warning("Invalid %s; expected a JSON list of objects", name)
        return []
    return value


def _bands_env() -> dict[str, str]:
    raw = os.getenv("AIRLOCK_PII_EGRESS_TOOL_BANDS", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid AIRLOCK_PII_EGRESS_TOOL_BANDS JSON; using no bands")
        return {}
    if not isinstance(value, dict):
        logger.warning("Invalid AIRLOCK_PII_EGRESS_TOOL_BANDS; using no bands")
        return {}
    return {
        str(tool).lower(): str(band).lower()
        for tool, band in value.items()
        if str(band).lower() in _VALID_BANDS
    }


def egress_mode() -> str:
    raw = os.getenv("AIRLOCK_PII_EGRESS_MODE", "observe").strip().lower()
    if raw not in _VALID_MODES:
        logger.warning("Invalid AIRLOCK_PII_EGRESS_MODE=%r; using 'observe'", raw)
        return "observe"
    return raw


def entity_type_from_placeholder(placeholder: str) -> str:
    """Return the class encoded in Airlock's numbered placeholder format."""
    if not (placeholder.startswith("<") and placeholder.endswith(">")):
        return "UNKNOWN"
    body = placeholder[1:-1]
    entity, sep, suffix = body.rpartition("_")
    return entity if sep and suffix.isdigit() else "UNKNOWN"


def _matches(entry: dict[str, Any], *, tool: str, path: str, entity_type: str) -> bool:
    def field(name: str, actual: str) -> bool:
        expected = str(entry.get(name, "*")).lower()
        return expected == "*" or expected == actual.lower()

    return (
        field("tool", tool)
        and field("path", path)
        and field("entity_type", entity_type)
    )


def decide(*, tool: str, path: str, placeholder: str) -> EgressDecision:
    """Decide whether one value may be rehydrated; never inspects the value."""
    entity_type = entity_type_from_placeholder(placeholder)
    for entry in _json_list_env("AIRLOCK_PII_EGRESS_BLOCKLIST"):
        if _matches(entry, tool=tool, path=path, entity_type=entity_type):
            return EgressDecision(False, "known_bad", entity_type, tool, path)

    if entity_type in _DEFAULT_BLOCKED_CLASSES:
        return EgressDecision(False, "sensitive_class", entity_type, tool, path)

    band = _bands_env().get(tool.lower())
    if band == "round_trip":
        return EgressDecision(True, "round_trip", entity_type, tool, path)
    if band == "exfil":
        for entry in _json_list_env("AIRLOCK_PII_EGRESS_ALLOWLIST"):
            if _matches(entry, tool=tool, path=path, entity_type=entity_type):
                return EgressDecision(True, "residual_allow", entity_type, tool, path)
        return EgressDecision(False, "exfil_not_allowlisted", entity_type, tool, path)
    return EgressDecision(False, "unknown_tool", entity_type, tool, path)
