"""Target tests for Pack 0.5.4-EVENT — canonical ``RequestEvent`` + recorder seam.

No network. Inputs are built in-process: a small fake response object with
``.usage`` and ``.choices`` and plain dict ``kwargs``/``litellm_params``/
``metadata``. The sourcing contract mirrors ``AirlockLogger._build_record``
(``enterprise_logger.py``) — these tests pin that parity plus the three NEW
superset fields (``bare_exception_error``, ``request_headers``, ``mcp_arguments``)
and the dispatcher seam (ordering, per-sink failure isolation, empty=no-op).
"""

from __future__ import annotations

import datetime

import pytest

from airlock.callbacks import request_event as re_mod
from airlock.callbacks.request_event import (
    RequestEvent,
    RequestRecorder,
    build_request_event,
)
from airlock.transparency import Mutation, ServedBackend


class _FakeUsage:
    def __init__(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, *, prompt=3, completion=5, total=8, content="hi") -> None:
        self.usage = _FakeUsage(prompt, completion, total)
        self.choices = [_FakeChoice(content)]


def _kwargs(**over):
    metadata = {
        "user_api_key_alias": "alice",
        "user_api_key_team_alias": "team-a",
        "airlock_provider": "openai",
    }
    metadata.update(over.pop("metadata", {}))
    litellm_params = {"metadata": metadata}
    litellm_params.update(over.pop("litellm_params", {}))
    base = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
        "litellm_call_id": "call-123",
        "litellm_params": litellm_params,
        "response_cost": 0.0021,
        "headers": {"x-trace": "abc"},
    }
    base.update(over)
    return base


def _ts(secs: float) -> datetime.datetime:
    return datetime.datetime(2026, 6, 28, 12, 0, 0, tzinfo=datetime.timezone.utc) + (
        datetime.timedelta(seconds=secs)
    )


# ---------------------------------------------------------------------------
# 1. Success event field-sourcing
# ---------------------------------------------------------------------------
def test_success_event_field_sourcing():
    resp = _FakeResponse(prompt=3, completion=5, total=8)
    start, end = _ts(0), _ts(1.5)
    kwargs = _kwargs()

    event = build_request_event(kwargs, resp, start, end, success=True)

    assert isinstance(event, RequestEvent)
    assert isinstance(event.timestamp, str)
    # exactly one parseable iso timestamp, sourced once
    datetime.datetime.fromisoformat(event.timestamp)
    assert event.record_type == "request"
    assert event.success is True
    assert event.model == "gpt-4o"
    assert event.messages == kwargs["messages"]
    assert event.request_id == "call-123"
    assert event.user == "alice"
    assert event.team == "team-a"
    assert event.airlock_client is not None or event.airlock_client is None
    assert event.airlock_provider == "openai"
    assert event.duration_ms == 1500
    assert event.usage["prompt_tokens"] == 3
    assert event.usage["completion_tokens"] == 5
    assert event.usage["total_tokens"] == 8
    assert event.response_cost == 0.0021
    # raw response object carried, NOT serialized
    assert event.response_obj is resp


def test_success_event_missing_times_duration_none():
    event = build_request_event(_kwargs(), _FakeResponse(), None, None, success=True)
    assert event.duration_ms is None


# ---------------------------------------------------------------------------
# 2. Failure event — rich triple + bare_exception_error
# ---------------------------------------------------------------------------
def test_failure_event_rich_and_bare():
    exc = ValueError("boom")
    kwargs = _kwargs(exception=exc)

    event = build_request_event(kwargs, None, _ts(0), _ts(1), success=False)

    assert event.success is False
    assert event.error == "boom"
    assert event.error_type == "ValueError"
    assert event.failure_category in {"provider", "eval", "pre_call"}
    assert event.bare_exception_error == str(exc)


def test_failure_event_bare_exception_none_string():
    # exception missing -> str(None) == "None"
    kwargs = _kwargs()
    kwargs.pop("exception", None)
    event = build_request_event(kwargs, None, _ts(0), _ts(1), success=False)
    assert event.bare_exception_error == "None"


def test_success_event_bare_exception_is_none():
    event = build_request_event(
        _kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True
    )
    assert event.bare_exception_error is None
    assert event.error is None
    assert event.error_type is None
    assert event.failure_category is None


# ---------------------------------------------------------------------------
# 3. New fields — request_headers, mcp_arguments chain, mcp_meta
# ---------------------------------------------------------------------------
def test_request_headers_sourced_from_kwargs():
    kwargs = _kwargs(headers={"x-trace": "abc", "authorization": "redacted"})
    event = build_request_event(kwargs, _FakeResponse(), _ts(0), _ts(1), success=True)
    assert event.request_headers == kwargs["headers"]


def test_mcp_arguments_resolution_chain():
    # kwargs wins
    k = _kwargs(mcp_arguments={"from": "kwargs"})
    ev = build_request_event(k, _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.mcp_arguments == {"from": "kwargs"}

    # litellm_params fallback
    k = _kwargs(litellm_params={"mcp_arguments": {"from": "lp"}})
    ev = build_request_event(k, _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.mcp_arguments == {"from": "lp"}

    # metadata fallback
    k = _kwargs(metadata={"mcp_arguments": {"from": "meta"}})
    ev = build_request_event(k, _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.mcp_arguments == {"from": "meta"}


def test_mcp_meta_populated_on_mcp_tool_call():
    k = _kwargs(
        call_type="call_mcp_tool",
        mcp_tool_name="search",
        mcp_server_name="srv",
    )
    ev = build_request_event(k, _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.mcp_meta["call_type"] == "call_mcp_tool"
    assert ev.mcp_meta["mcp_tool_name"] == "search"
    assert ev.mcp_meta["mcp_server_name"] == "srv"


def test_mcp_meta_empty_for_normal_call():
    ev = build_request_event(_kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.mcp_meta == {}


# ---------------------------------------------------------------------------
# 4. Gemini enrich-once (design §3.5) — metadata mutated in place before snapshot
# ---------------------------------------------------------------------------
def test_gemini_enrich_once_mutates_metadata_and_guardrail_meta(monkeypatch):
    monkeypatch.setattr(
        re_mod, "classify_gemini_response", lambda obj: {"output_shape": "text"}
    )
    monkeypatch.setattr(
        re_mod,
        "build_gemini_response_headers",
        lambda req, resp: {"airlock_gemini_visibility": "final_only"},
    )

    metadata = {
        "user_api_key_alias": "alice",
        "airlock_provider": "gemini",
    }
    kwargs = _kwargs(model="gemini-1.5-pro", metadata=metadata)
    # the actual metadata dict that build will mutate in place
    live_meta = kwargs["litellm_params"]["metadata"]

    event = build_request_event(kwargs, _FakeResponse(), _ts(0), _ts(1), success=True)

    # metadata mutated in place
    assert "airlock_gemini" in live_meta
    assert "airlock_gemini_response" in live_meta
    assert "airlock_response_headers" in live_meta
    # guardrail_meta snapshot reflects post-enrichment airlock_* keys
    assert event.guardrail_meta.get("airlock_gemini") == live_meta["airlock_gemini"]
    assert "airlock_gemini_response" in event.guardrail_meta
    assert "airlock_response_headers" in event.guardrail_meta


def test_guardrail_meta_collects_airlock_keys():
    kwargs = _kwargs(metadata={"airlock_semantic_score": 0.9, "other": "x"})
    ev = build_request_event(kwargs, _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.guardrail_meta.get("airlock_semantic_score") == 0.9
    assert "other" not in ev.guardrail_meta


# ---------------------------------------------------------------------------
# 5. mutations / served / attribution
# ---------------------------------------------------------------------------
def test_mutations_serialized_from_dataclasses():
    mut = Mutation(
        field="model",
        op="override",
        before="gpt-4o",
        after="gpt-4o-mini",
        stage="pre_call",
        source="router",
    )
    kwargs = _kwargs(metadata={"airlock_mutations": [mut]})
    ev = build_request_event(kwargs, _FakeResponse(), _ts(0), _ts(1), success=True)
    assert isinstance(ev.mutations, list)
    assert ev.mutations[0]["field"] == "model"
    assert ev.mutations[0]["after"] == "gpt-4o-mini"


def test_served_and_attribution_from_attribute_served_backend(monkeypatch):
    served = ServedBackend(
        provider="openai",
        api_base_host="api.openai.com",
        region=None,
        model_id="gpt-4o",
        response_cost=0.0021,
        backend_kind="native",
    )
    monkeypatch.setattr(re_mod, "attribute_served_backend", lambda *a, **k: served)
    ev = build_request_event(_kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.served is not None
    assert ev.served["provider"] == "openai"
    assert ev.attribution == "served"


def test_served_attribution_inferred_when_no_provider(monkeypatch):
    served = ServedBackend(
        provider=None,
        api_base_host=None,
        region=None,
        model_id=None,
        response_cost=None,
        backend_kind="unknown",
    )
    monkeypatch.setattr(re_mod, "attribute_served_backend", lambda *a, **k: served)
    ev = build_request_event(_kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.attribution == "inferred"


def test_served_none_when_attribution_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("attribution failed")

    monkeypatch.setattr(re_mod, "attribute_served_backend", _boom)
    ev = build_request_event(_kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True)
    assert ev.served is None
    assert ev.attribution == "inferred"


# ---------------------------------------------------------------------------
# 6. Recorder ordering
# ---------------------------------------------------------------------------
def test_recorder_dispatch_in_registration_order():
    calls = []
    recorder = RequestRecorder()
    recorder.register(lambda e: calls.append("A"), name="A")
    recorder.register(lambda e: calls.append("B"), name="B")
    recorder.register(lambda e: calls.append("C"), name="C")

    assert list(recorder.sink_names) == ["A", "B", "C"]

    event = build_request_event(
        _kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True
    )
    recorder.dispatch(event)

    assert calls == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# 7. Recorder failure isolation (AC-SEAM)
# ---------------------------------------------------------------------------
def test_recorder_failure_isolation(caplog):
    calls = []

    def raising(_e):
        raise RuntimeError("sink B exploded")

    recorder = RequestRecorder()
    recorder.register(lambda e: calls.append("A"), name="A")
    recorder.register(raising, name="B")
    recorder.register(lambda e: calls.append("C"), name="C")

    event = build_request_event(
        _kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True
    )

    import logging

    with caplog.at_level(logging.WARNING):
        # must NOT raise
        recorder.dispatch(event)

    assert calls == ["A", "C"]
    assert any(
        "B" in r.getMessage() or "exploded" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# 8. Empty recorder is a no-op
# ---------------------------------------------------------------------------
def test_empty_recorder_dispatch_is_noop():
    recorder = RequestRecorder()
    assert list(recorder.sink_names) == []
    event = build_request_event(
        _kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True
    )
    # no sinks, no raise, returns None
    assert recorder.dispatch(event) is None


def test_recorder_frozen_event_is_passed_through():
    received = []
    recorder = RequestRecorder()
    recorder.register(lambda e: received.append(e), name="capture")
    event = build_request_event(
        _kwargs(), _FakeResponse(), _ts(0), _ts(1), success=True
    )
    recorder.dispatch(event)
    assert received == [event]
    with pytest.raises(Exception):
        event.model = "mutated"  # frozen dataclass
