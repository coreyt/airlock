"""Parameterized advisory analysis tools (0.5.9 finding F-1, Part B).

Part A bounded the tool loop. Part B gives the tools real arguments — filters,
limits, and a log-backed query — validated against strict schemas before
anything runs.

**Why this waited for the bounded reader.** Parameters without a bound behind
them let a model request an arbitrarily large slice, which is exactly the
memory exhaustion finding F-4 closed. Every log-backed argument here is served
by :mod:`airlock.log_query`, and every limit is clamped server-side rather than
trusted from the model.

**Truncation reaches the model.** Results are wrapped in an envelope carrying
``returned``, ``total_available``, and ``truncated``. Part A capped tool
results by slicing the serialized JSON at a byte offset, which produced invalid
JSON the model could not parse and told it nothing about what was dropped — a
model that silently receives 4 of 40 optimizations draws confident conclusions
about traffic it never saw.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from airlock.log_query import LogQuery, query_logs

#: Hard ceiling on any ``limit`` argument, whatever the model asks for.
MAX_LIMIT = 100

#: Hard ceiling on any ``days`` argument.
MAX_DAYS = 90

#: Default number of items returned when the model gives no ``limit``.
DEFAULT_LIMIT = 20


class ToolArgumentError(ValueError):
    """A tool call's arguments failed strict validation."""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_LIMIT_PROPERTY = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_LIMIT,
    "description": (
        f"Maximum items to return (1-{MAX_LIMIT}, default {DEFAULT_LIMIT}). "
        "The response reports total_available so you can tell whether you saw "
        "everything."
    ),
}

_DAYS_PROPERTY = {
    "type": "integer",
    "minimum": 1,
    "maximum": MAX_DAYS,
    "description": f"Days of history to scan (1-{MAX_DAYS}).",
}


#: name -> (JSON Schema for parameters, human description).
#:
#: Schemas are strict: ``additionalProperties: false`` plus explicit types and
#: bounds, so an out-of-range or misspelled argument is rejected before any
#: scan runs rather than being silently coerced.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "summary": {
        "description": "Read the derived summary section. Aggregate counts only.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "semantic_insights": {
        "description": (
            "Read semantic classifier aggregates. Note that a classifier which "
            "could not answer is counted as unavailable, never as clean."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "optimizations": {
        "description": (
            "Read proposed optimizations, most impactful first. Filter by "
            "category or impact to narrow a large result set."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": _LIMIT_PROPERTY,
                "category": {
                    "type": "string",
                    "enum": ["reliability", "performance", "cost"],
                    "description": "Return only optimizations in this category.",
                },
                "impact": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Return only optimizations at this impact level.",
                },
            },
            "additionalProperties": False,
        },
    },
    "hypotheses": {
        "description": (
            "Read generated hypotheses. These are advisory proposals to test, "
            "not established findings."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": _LIMIT_PROPERTY,
                "min_confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Return only hypotheses at or above this confidence.",
                },
            },
            "additionalProperties": False,
        },
    },
    "query_requests": {
        "description": (
            "Count request records matching a filter, over a bounded window. "
            "Returns aggregate counts and identifiers only — never prompt text, "
            "response bodies, or credentials. Use this to check a specific model "
            "or client rather than reasoning from the precomputed sections alone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": _DAYS_PROPERTY,
                "model": {
                    "type": "string",
                    "description": "Exact model name to filter on.",
                },
                "client": {
                    "type": "string",
                    "description": "Exact Airlock client ID to filter on.",
                },
                "only_failures": {
                    "type": "boolean",
                    "description": "Restrict to failed requests.",
                },
                "limit": _LIMIT_PROPERTY,
            },
            "additionalProperties": False,
        },
    },
}

ALLOWED_TOOLS = frozenset(TOOL_SCHEMAS)

#: Tools answered from the precomputed report rather than a log scan.
_PAYLOAD_TOOLS = frozenset(
    {"summary", "semantic_insights", "optimizations", "hypotheses"}
)


def tool_definitions() -> list[dict[str, Any]]:
    """OpenAI-compatible function definitions for the analyzer tool loop."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for name, spec in sorted(TOOL_SCHEMAS.items())
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_arguments(name: str, parsed: Any) -> dict[str, Any]:
    """Validate *parsed* against the schema for *name*.

    Deliberately hand-written rather than pulled from a JSON Schema library:
    the surface is five tools with a dozen fields, and the failure mode that
    matters is an unbounded value reaching a log scan. Raises
    :class:`ToolArgumentError` with a message the model can act on.
    """
    if name not in TOOL_SCHEMAS:
        raise ToolArgumentError(f"unknown tool {name!r}")
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ToolArgumentError(f"{name}: arguments must be a JSON object")

    schema = TOOL_SCHEMAS[name]["parameters"]
    properties: dict[str, Any] = schema["properties"]

    unknown = set(parsed) - set(properties)
    if unknown:
        raise ToolArgumentError(
            f"{name}: unknown argument(s) {sorted(unknown)}; "
            f"allowed: {sorted(properties) or 'none'}"
        )

    clean: dict[str, Any] = {}
    for key, value in parsed.items():
        spec = properties[key]
        expected = spec["type"]

        if expected == "integer":
            # bool is an int subclass; accepting it would silently mean 0/1.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolArgumentError(f"{name}.{key}: expected an integer")
        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ToolArgumentError(f"{name}.{key}: expected a number")
        elif expected == "string":
            if not isinstance(value, str):
                raise ToolArgumentError(f"{name}.{key}: expected a string")
        elif expected == "boolean":
            if not isinstance(value, bool):
                raise ToolArgumentError(f"{name}.{key}: expected a boolean")

        if "enum" in spec and value not in spec["enum"]:
            raise ToolArgumentError(
                f"{name}.{key}: must be one of {sorted(spec['enum'])}"
            )
        # Out-of-range values are clamped rather than rejected: the bound is
        # what protects the reader, and a rejection would spend a tool call to
        # teach the model a limit the schema already states.
        if "minimum" in spec and value < spec["minimum"]:
            value = spec["minimum"]
        if "maximum" in spec and value > spec["maximum"]:
            value = spec["maximum"]

        clean[key] = value

    return clean


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _envelope(
    name: str, items: list[Any], total: int, *, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Wrap a result so the model can tell a partial view from a complete one."""
    body: dict[str, Any] = {
        "tool": name,
        "returned": len(items),
        "total_available": total,
        "truncated": len(items) < total,
        "data": items,
    }
    if len(items) < total:
        body["note"] = (
            f"Showing {len(items)} of {total}. Do not describe this as the "
            "complete picture; narrow the filter or raise limit to see more."
        )
    if extra:
        body.update(extra)
    return body


def _filter_optimizations(rows: list[Any], args: dict[str, Any]) -> list[Any]:
    category, impact = args.get("category"), args.get("impact")
    out = rows
    if category:
        out = [r for r in out if isinstance(r, dict) and r.get("category") == category]
    if impact:
        out = [r for r in out if isinstance(r, dict) and r.get("impact") == impact]
    return out


def _filter_hypotheses(rows: list[Any], args: dict[str, Any]) -> list[Any]:
    floor = args.get("min_confidence")
    if floor is None:
        return rows
    return [
        r
        for r in rows
        if isinstance(r, dict) and float(r.get("confidence") or 0.0) >= floor
    ]


def execute(
    name: str,
    args: dict[str, Any],
    *,
    payload: dict[str, Any],
    log_dir: str | None = None,
) -> dict[str, Any]:
    """Run a validated tool call and return a JSON-serializable envelope."""
    if name in _PAYLOAD_TOOLS:
        section = payload.get(name)

        # Single-object sections have nothing to filter or page.
        if not isinstance(section, list):
            return {"tool": name, "returned": 1, "truncated": False, "data": section}

        rows = section
        if name == "optimizations":
            rows = _filter_optimizations(rows, args)
        elif name == "hypotheses":
            rows = _filter_hypotheses(rows, args)

        total = len(rows)
        limit = int(args.get("limit", DEFAULT_LIMIT))
        return _envelope(name, rows[:limit], total)

    if name == "query_requests":
        return _query_requests(args, log_dir=log_dir)

    raise ToolArgumentError(f"unknown tool {name!r}")


def _query_requests(args: dict[str, Any], *, log_dir: str | None) -> dict[str, Any]:
    """Bounded, filtered log scan returning counts and identifiers only."""
    if not log_dir:
        return {
            "tool": "query_requests",
            "returned": 0,
            "total_available": 0,
            "truncated": False,
            "data": [],
            "note": "No log directory configured; this tool is unavailable.",
        }

    model = args.get("model")
    client = args.get("client")
    only_failures = bool(args.get("only_failures"))

    def predicate(record: dict[str, Any]) -> bool:
        if model and record.get("model") != model:
            return False
        if client and record.get("airlock_client") != client:
            return False
        if only_failures and record.get("success") is not False:
            return False
        return True

    # Filtering happens during the scan, inside the bounded reader — the whole
    # reason parameters could not ship before F-4.
    page = query_logs(
        LogQuery(
            days=int(args.get("days", 7)),
            predicate=predicate,
            directory=Path(log_dir),
        )
    )

    limit = int(args.get("limit", DEFAULT_LIMIT))
    by_model: dict[str, int] = {}
    by_client: dict[str, int] = {}
    failures = 0
    for record in page.records:
        by_model[str(record.get("model", "unknown"))] = (
            by_model.get(str(record.get("model", "unknown")), 0) + 1
        )
        by_client[str(record.get("airlock_client", "unknown"))] = (
            by_client.get(str(record.get("airlock_client", "unknown")), 0) + 1
        )
        if record.get("success") is False:
            failures += 1

    # Identifiers only — never message content. Reports carry counts and
    # identifiers so a reviewer looks a request up deliberately.
    samples = [
        {
            "timestamp": r.get("timestamp"),
            "request_id": r.get("request_id"),
            "model": r.get("model"),
            "client": r.get("airlock_client"),
            "success": r.get("success"),
            "error_type": r.get("error_type"),
        }
        for r in page.records[-limit:]
    ]

    return _envelope(
        "query_requests",
        samples,
        len(page.records),
        extra={
            "matched": len(page.records),
            "failures": failures,
            "by_model": by_model,
            "by_client": by_client,
            "window": {"truncated": page.truncated, "limit_hit": page.limit_hit},
            **(
                {
                    "scan_note": (
                        "The scan itself hit the reader's bound, so these counts "
                        "are a partial view of the window."
                    )
                }
                if page.truncated
                else {}
            ),
        },
    )
