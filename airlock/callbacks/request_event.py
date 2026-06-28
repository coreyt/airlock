"""Canonical ``RequestEvent`` + the single recorder/dispatcher seam (0.5.4-EVENT).

Airlock rebuilds the same per-request telemetry record independently in several
sinks (enterprise / fathom / s3 / sql) plus two side channels (mutation ledger,
metrics). This module collapses that duplication: ``build_request_event`` sources
the record **once** into a frozen ``RequestEvent`` superset, and ``RequestRecorder``
fans one event out to registered sinks with per-sink failure isolation.

This pack is **behavior-preserving scaffolding** — it defines the model + builder +
seam and is unit-tested against in-memory sinks. No existing sink is migrated and
nothing is installed into the live LiteLLM callback manager here; that is the work
of the MIGRATE-* packs. See ``dev/notes/design-request-event-bus.md`` (rev 4) for
the authoritative contract (§3.5/§3.8–§3.11/§4/§5/§5a/§5b).

Field sourcing mirrors ``AirlockLogger._build_record`` verbatim and **reuses** its
helpers rather than reimplementing them, so the event is a single source of truth.
"""

from __future__ import annotations

import dataclasses
import datetime
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from airlock.callbacks.enterprise_logger import (
    _get_airlock_client,
    _normalize_failure,
    _serialize,
)
from airlock.fast.router import infer_provider
from airlock.gemini_interface import (
    build_gemini_response_headers,
    classify_gemini_response,
    is_gemini_provider,
)
from airlock.transparency import attribute_served_backend

logger = logging.getLogger("airlock.logger")

# A sink is a callable that consumes one event and returns nothing.
Sink = Callable[["RequestEvent"], None]


@dataclass(frozen=True)
class RequestEvent:
    """The canonical, sourced-once per-request telemetry event (the **superset**).

    Each sink later projects its own historical subset from this one event; the
    field set here is the union of every current builder + the mutation ledger +
    the per-request metrics. Frozen (not pydantic) to match the ``transparency.py``
    dataclasses and stay cheap on the hot path.
    """

    # identity / lifecycle
    timestamp: str
    record_type: str
    success: bool
    start_time: Any
    end_time: Any
    duration_ms: int | None
    # request
    model: str
    messages: Any
    request_id: Any
    user: Any
    team: Any
    airlock_client: str | None
    airlock_provider: str | None
    request_headers: Any
    # response — the RAW response object (each projection serializes/extracts, §3.10)
    response_obj: Any
    usage: dict[str, int]
    response_cost: Any
    # failure — both forms carried (§3.9)
    error: str | None
    error_type: str | None
    failure_category: str | None
    bare_exception_error: str | None
    # enrichment (computed once, pre-fanout — §3.5)
    guardrail_meta: dict[str, Any]
    mcp_meta: dict[str, Any]
    mcp_arguments: Any
    # transparency
    mutations: list[Any]
    served: dict[str, Any] | None
    attribution: str


def build_request_event(
    kwargs: dict,
    response_obj: Any,
    start_time: Any,
    end_time: Any,
    *,
    success: bool,
) -> RequestEvent:
    """Source one ``RequestEvent`` from the success/failure callback inputs.

    Mirrors ``AirlockLogger._build_record`` field-for-field for the shared fields
    (reusing its helpers), runs the Gemini enrich-once block **before** snapshotting
    ``guardrail_meta`` (§3.5), carries the raw ``response_obj`` (§3.10), and adds the
    three NEW superset fields: ``bare_exception_error`` (§3.9), ``request_headers``
    and ``mcp_arguments`` (§3.8).
    """
    litellm_params = kwargs.get("litellm_params", {}) or {}
    metadata = litellm_params.get("metadata", {}) or {}
    airlock_client = _get_airlock_client(metadata, kwargs)

    error = None
    error_type = None
    failure_category = None
    bare_exception_error = None
    if not success:
        error, error_type, failure_category = _normalize_failure(kwargs, response_obj)
        # s3/sql project the raw exception string verbatim (incl. str(None) == "None")
        bare_exception_error = str(kwargs.get("exception"))

    # Token usage — same getattr-with-0 sourcing as the enterprise builder.
    usage: dict[str, int] = {}
    if response_obj and hasattr(response_obj, "usage") and response_obj.usage:
        u = response_obj.usage
        usage = {
            "prompt_tokens": getattr(u, "prompt_tokens", 0),
            "completion_tokens": getattr(u, "completion_tokens", 0),
            "total_tokens": getattr(u, "total_tokens", 0),
        }

    provider = metadata.get("airlock_provider") or infer_provider(
        kwargs.get("model", "unknown")
    )

    # Enrich-once (§3.5): reproduce the builder's Gemini block, mutating `metadata`
    # in place BEFORE snapshotting guardrail_meta so later sinks see the enriched
    # airlock_* keys.
    if success and is_gemini_provider(kwargs.get("model"), provider):
        request_meta = metadata.get("airlock_gemini") or {
            "mode": "balanced",
            "visibility": "final_only",
            "allow_empty_text": False,
            "mapping_source": "implicit",
            "explicit_controls": [],
            "provider": "gemini",
            "model": kwargs.get("model", "unknown"),
        }
        response_meta = classify_gemini_response(response_obj) or {
            "output_shape": "unknown",
            "empty_text_success": False,
        }
        metadata["airlock_gemini"] = request_meta
        metadata["airlock_gemini_response"] = response_meta
        response_headers = metadata.setdefault("airlock_response_headers", {})
        response_headers.update(
            build_gemini_response_headers(request_meta, response_meta)
        )

    # Snapshot AFTER the enrich step.
    guardrail_meta = {k: v for k, v in metadata.items() if k.startswith("airlock_")}

    # MCP tool call metadata — same conditions as the builder.
    call_type = kwargs.get("call_type", "")
    mcp_meta: dict[str, Any] = {}
    if call_type == "call_mcp_tool" or "mcp_tool_name" in kwargs:
        mcp_meta["call_type"] = call_type or "call_mcp_tool"
        mcp_meta["mcp_tool_name"] = (
            kwargs.get("mcp_tool_name")
            or litellm_params.get("mcp_tool_name")
            or metadata.get("mcp_tool_name")
        )
        mcp_meta["mcp_server_name"] = (
            kwargs.get("mcp_server_name")
            or litellm_params.get("mcp_server_name")
            or metadata.get("mcp_server_name")
        )

    # NEW (§3.8): resolved mcp_arguments chain — kwargs -> litellm_params -> metadata.
    mcp_arguments = (
        kwargs.get("mcp_arguments")
        or litellm_params.get("mcp_arguments")
        or metadata.get("mcp_arguments")
    )

    # Transparency: mutation ledger (asdict dataclasses else serialize).
    ledger = metadata.get("airlock_mutations") or []
    mutations = [
        dataclasses.asdict(m) if dataclasses.is_dataclass(m) else _serialize(m)
        for m in ledger
    ]

    # Served-backend attribution — wrapped so logging never crashes the build.
    try:
        served_backend = attribute_served_backend(
            response_obj, cost_fallback=kwargs.get("response_cost")
        )
    except Exception:  # logging must never crash the record build
        logger.debug("served-backend attribution failed", exc_info=True)
        served_backend = None
    served = dataclasses.asdict(served_backend) if served_backend is not None else None
    attribution = (
        "served"
        if served is not None and served.get("provider") is not None
        else "inferred"
    )

    duration_ms = (
        int((end_time - start_time).total_seconds() * 1000)
        if start_time and end_time
        else None
    )

    return RequestEvent(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        record_type="request",
        success=success,
        start_time=start_time,
        end_time=end_time,
        duration_ms=duration_ms,
        model=kwargs.get("model", "unknown"),
        messages=kwargs.get("messages"),
        request_id=kwargs.get("litellm_call_id"),
        user=metadata.get("user_api_key_alias") or metadata.get("user_api_key_user_id"),
        team=metadata.get("user_api_key_team_alias"),
        airlock_client=airlock_client,
        airlock_provider=provider,
        request_headers=kwargs.get("headers"),
        response_obj=response_obj,
        usage=usage,
        response_cost=kwargs.get("response_cost", 0),
        error=error,
        error_type=error_type,
        failure_category=failure_category,
        bare_exception_error=bare_exception_error,
        guardrail_meta=guardrail_meta,
        mcp_meta=mcp_meta,
        mcp_arguments=mcp_arguments,
        mutations=mutations,
        served=served,
        attribution=attribution,
    )


@dataclass
class _Registration:
    name: str
    sink: Sink


class RequestRecorder:
    """The single dispatch seam: an ordered registry of sinks (§5/§5a).

    ``register`` appends; ``dispatch`` fans one event out in registration order with
    per-sink failure isolation — a raising sink is caught, logged, and never
    propagates to the caller or stops the other sinks (AC-SEAM). An empty recorder
    dispatch is a no-op and ``dispatch`` itself never raises. This is the seam
    *mechanism* only — it is NOT installed into the live LiteLLM callback manager in
    this pack.
    """

    def __init__(self) -> None:
        self._sinks: list[_Registration] = []
        self._lock = threading.Lock()

    def register(self, sink: Sink, *, name: str) -> None:
        """Append a sink; dispatch order equals registration order (deterministic)."""
        with self._lock:
            self._sinks.append(_Registration(name=name, sink=sink))

    @property
    def sinks(self) -> list[Sink]:
        with self._lock:
            return [reg.sink for reg in self._sinks]

    @property
    def sink_names(self) -> list[str]:
        with self._lock:
            return [reg.name for reg in self._sinks]

    def dispatch(self, event: RequestEvent) -> None:
        """Fan ``event`` out to every sink in order; never raise (telemetry safety)."""
        # Snapshot the sink set under the lock, then iterate the snapshot OUTSIDE the
        # lock so a slow/raising sink never holds it and a concurrent/reentrant
        # register() can't perturb an in-flight dispatch.
        with self._lock:
            sinks = tuple(self._sinks)
        for reg in sinks:
            try:
                reg.sink(event)
            except Exception:  # one failing sink must not break the request or others
                logger.warning(
                    "request sink %r raised during dispatch; continuing",
                    reg.name,
                    exc_info=True,
                )
        return None
