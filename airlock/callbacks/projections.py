"""Pure per-sink projections over the canonical ``RequestEvent`` (0.5.4-MIGRATE).

Each function reproduces today's builder output **field-for-field** from the one
sourced-once ``RequestEvent`` — no kwargs re-read — so the live
``AirlockLogger._build_record`` / fathom ``_fathom_properties`` builders can later
be replaced by these projections without any wire-level change. This pack is
strictly additive: the old builders stay in place as the equivalence oracle (see
``tests/test_projections_equiv.py``); rewiring the sinks is pack 2b.

The only accepted divergence vs the live builders is ``timestamp`` (the registered
convergence, design §3.4): both carry an ISO ``timestamp`` sourced once on the
event.
"""

from __future__ import annotations

from typing import Any

from airlock.callbacks.enterprise_logger import _redact_record, _serialize
from airlock.callbacks.fathom_logger import _env_flag, _json_text, _response_text
from airlock.callbacks.request_event import RequestEvent


def project_enterprise(event: RequestEvent) -> dict[str, Any]:
    """Reproduce ``AirlockLogger._build_record`` output from the event (§3.10)."""
    record: dict[str, Any] = {
        "timestamp": event.timestamp,
        "record_type": event.record_type,
        "success": event.success,
        "model": event.model,
        "user": event.user,
        "team": event.team,
        "request_id": event.request_id,
        "messages": event.messages,
        "response": _serialize(event.response_obj) if event.response_obj else None,
        "error": event.error,
        "error_type": event.error_type,
        "failure_category": event.failure_category,
        "airlock_provider": event.airlock_provider,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "duration_ms": event.duration_ms,
        **event.usage,
        **event.mcp_meta,
        **event.guardrail_meta,
    }
    record["mutations"] = event.mutations
    record["served"] = event.served
    record["attribution"] = event.attribution
    record["airlock_client"] = event.airlock_client
    return record


def project_s3(event: RequestEvent) -> dict[str, Any]:
    """Reproduce ``AirlockS3Logger._build_record`` output from the event (§3.9).

    s3's narrow set carries NO guardrail_meta/mcp/served/provider/record_type; the
    key order matches the old builder exactly. ``error`` is the bare exception string
    (``str(exception)|None``), and ``_redact_record`` is applied last.
    """
    record: dict[str, Any] = {
        "timestamp": event.timestamp,
        "success": event.success,
        "model": event.model,
        "user": event.user,
        "team": event.team,
        "request_id": event.request_id,
        "messages": event.messages,
        "response": _serialize(event.response_obj) if event.response_obj else None,
        "error": event.bare_exception_error,
        "duration_ms": event.duration_ms,
        **event.usage,
    }
    return _redact_record(record)


def project_fathom(event: RequestEvent) -> dict[str, Any]:
    """Reproduce ``_fathom_properties`` output from the event (env-gated, §3.11)."""
    properties: dict[str, Any] = {
        "timestamp": event.timestamp,
        "success": event.success,
        "error_flag": not event.success,
        "model": event.model,
        "airlock_provider": event.airlock_provider,
        "request_id": event.request_id,
        "call_id": event.request_id,
        "prompt_tokens": event.usage.get("prompt_tokens", 0),
        "completion_tokens": event.usage.get("completion_tokens", 0),
        "total_tokens": event.usage.get("total_tokens", 0),
        "cost": event.response_cost,
        "duration_ms": event.duration_ms,
        "failure_category": event.failure_category,
        "call_type": event.mcp_meta.get("call_type"),
        "mcp_tool_name": event.mcp_meta.get("mcp_tool_name"),
        "mcp_server_name": event.mcp_meta.get("mcp_server_name"),
    }

    if _env_flag("AIRLOCK_FATHOM_STORE_CLIENT"):
        properties["airlock_client"] = event.airlock_client

    if _env_flag("AIRLOCK_FATHOM_STORE_USER_TEAM"):
        properties["user"] = event.user
        properties["team"] = event.team

    if _env_flag("AIRLOCK_FATHOM_STORE_ERROR_DETAILS"):
        properties["error_type"] = event.error_type
        properties["error"] = event.error

    if _env_flag("AIRLOCK_FATHOM_STORE_MESSAGES"):
        properties["messages_json"] = _json_text(event.messages)

    if _env_flag("AIRLOCK_FATHOM_STORE_RESPONSE_TEXT"):
        properties["response_text"] = _response_text(event.response_obj)

    if _env_flag("AIRLOCK_FATHOM_STORE_HEADERS"):
        properties["headers_json"] = _json_text(event.request_headers)

    if _env_flag("AIRLOCK_FATHOM_STORE_MCP_PAYLOADS"):
        properties["mcp_arguments_json"] = _json_text(event.mcp_arguments)

    return {key: value for key, value in properties.items() if value is not None}
