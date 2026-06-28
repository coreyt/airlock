"""Golden equivalence tests for Pack 0.5.4-MIGRATE-entfathom-project.

Prove the PURE projections in ``airlock.callbacks.projections`` reproduce the
LIVE builders field-for-field:

* ``project_enterprise(event)`` == ``AirlockLogger._build_record(...)``
* ``project_fathom(event)``     == ``_fathom_properties(...)``

…ignoring ONLY ``timestamp`` (the one registered convergence, design §3.4),
across a representative request set + the 7-flag ``AIRLOCK_FATHOM_STORE_*``
matrix. No network: inputs are built in-process.

The event and the old function are built from the SAME inputs. ``kwargs`` is
deep-copied per call so the in-place Gemini enrich can't cross-contaminate, while
the raw response object is SHARED so ``_serialize``/``str(obj)`` is identical on
both sides (the only divergence is ``timestamp``).
"""

from __future__ import annotations

import copy
import datetime

import pytest

from airlock.callbacks.enterprise_logger import AirlockLogger
from airlock.callbacks.fathom_logger import _fathom_properties
from airlock.callbacks.projections import project_enterprise, project_fathom
from airlock.callbacks.request_event import build_request_event
from airlock.transparency import Mutation


# ---------------------------------------------------------------------------
# In-process fixtures (mirror tests/test_request_event.py shapes)
# ---------------------------------------------------------------------------
class _FakeUsage:
    def __init__(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _FakeMessage:
    def __init__(self, content) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, *, prompt=3, completion=5, total=8, content="hi") -> None:
        self.usage = _FakeUsage(prompt, completion, total)
        self.choices = [_FakeChoice(content)]


def _ts(secs: float) -> datetime.datetime:
    return datetime.datetime(2026, 6, 28, 12, 0, 0, tzinfo=datetime.timezone.utc) + (
        datetime.timedelta(seconds=secs)
    )


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
        "headers": {"x-trace": "abc", "authorization": "redacted"},
    }
    base.update(over)
    return base


# Each entry: (id, make_inputs) where make_inputs() -> (kwargs, resp, start, end, success)
def _plain_success():
    return _kwargs(), _FakeResponse(), _ts(0), _ts(1.5), True


def _provider_failure():
    return _kwargs(exception=ValueError("boom")), None, _ts(0), _ts(1), False


def _pre_call_failure():
    # no exception, no response -> _normalize_failure category == "pre_call"
    k = _kwargs()
    k.pop("exception", None)
    return k, None, _ts(0), _ts(1), False


def _mcp_call():
    k = _kwargs(
        call_type="call_mcp_tool",
        mcp_tool_name="search",
        mcp_server_name="srv",
        mcp_arguments={"q": "weather"},
    )
    return k, _FakeResponse(), _ts(0), _ts(0.4), True


def _gemini_success():
    k = _kwargs(model="gemini-1.5-pro", metadata={"airlock_provider": "gemini"})
    return k, _FakeResponse(), _ts(0), _ts(2), True


def _mutations_and_served():
    mut = Mutation(
        field="model",
        op="override",
        before="gpt-4o",
        after="gpt-4o-mini",
        stage="pre_call",
        source="router",
    )
    k = _kwargs(metadata={"airlock_mutations": [mut]})
    return k, _FakeResponse(), _ts(0), _ts(1), True


def _guardrail_keys():
    k = _kwargs(
        metadata={
            "airlock_semantic_score": 0.91,
            "airlock_priority": "high",
            "airlock_failover": {"attempts": 2},
            "not_airlock": "ignored",
        }
    )
    return k, _FakeResponse(), _ts(0), _ts(1), True


REQUEST_SET = [
    ("plain_success", _plain_success),
    ("provider_failure", _provider_failure),
    ("pre_call_failure", _pre_call_failure),
    ("mcp_call", _mcp_call),
    ("gemini_success", _gemini_success),
    ("mutations_and_served", _mutations_and_served),
    ("guardrail_keys", _guardrail_keys),
]


# 7-flag AIRLOCK_FATHOM_STORE_* matrix: all-off, all-on, two mixed.
_ALL_FLAGS = [
    "AIRLOCK_FATHOM_STORE_CLIENT",
    "AIRLOCK_FATHOM_STORE_USER_TEAM",
    "AIRLOCK_FATHOM_STORE_ERROR_DETAILS",
    "AIRLOCK_FATHOM_STORE_MESSAGES",
    "AIRLOCK_FATHOM_STORE_RESPONSE_TEXT",
    "AIRLOCK_FATHOM_STORE_HEADERS",
    "AIRLOCK_FATHOM_STORE_MCP_PAYLOADS",
]

_ENV_MATRIX = [
    ("all_off", {f: "0" for f in _ALL_FLAGS}),
    ("all_on", {f: "1" for f in _ALL_FLAGS}),
    (
        "mixed_a",
        {
            "AIRLOCK_FATHOM_STORE_CLIENT": "1",
            "AIRLOCK_FATHOM_STORE_USER_TEAM": "0",
            "AIRLOCK_FATHOM_STORE_ERROR_DETAILS": "1",
            "AIRLOCK_FATHOM_STORE_MESSAGES": "0",
            "AIRLOCK_FATHOM_STORE_RESPONSE_TEXT": "1",
            "AIRLOCK_FATHOM_STORE_HEADERS": "0",
            "AIRLOCK_FATHOM_STORE_MCP_PAYLOADS": "1",
        },
    ),
    (
        "mixed_b",
        {
            "AIRLOCK_FATHOM_STORE_CLIENT": "0",
            "AIRLOCK_FATHOM_STORE_USER_TEAM": "1",
            "AIRLOCK_FATHOM_STORE_ERROR_DETAILS": "0",
            "AIRLOCK_FATHOM_STORE_MESSAGES": "1",
            "AIRLOCK_FATHOM_STORE_RESPONSE_TEXT": "0",
            "AIRLOCK_FATHOM_STORE_HEADERS": "1",
            "AIRLOCK_FATHOM_STORE_MCP_PAYLOADS": "0",
        },
    ),
]


def _enterprise_pair(inputs):
    kwargs, resp, start, end, success = inputs
    expected = AirlockLogger._build_record(
        copy.deepcopy(kwargs), resp, start, end, success=success
    )
    event = build_request_event(
        copy.deepcopy(kwargs), resp, start, end, success=success
    )
    got = project_enterprise(event)
    return expected, got


def _fathom_pair(inputs):
    kwargs, resp, start, end, success = inputs
    expected = _fathom_properties(
        copy.deepcopy(kwargs), resp, start, end, success=success
    )
    event = build_request_event(
        copy.deepcopy(kwargs), resp, start, end, success=success
    )
    got = project_fathom(event)
    return expected, got


def _strip_ts(d: dict) -> dict:
    d = dict(d)
    d.pop("timestamp", None)
    return d


@pytest.mark.parametrize("name,make_inputs", REQUEST_SET)
def test_project_enterprise_matches_build_record(name, make_inputs):
    expected, got = _enterprise_pair(make_inputs())
    # timestamp is the ONLY accepted divergence (registered convergence §3.4)
    assert expected.get("timestamp") is not None
    assert got.get("timestamp") is not None
    assert _strip_ts(got) == _strip_ts(expected)


@pytest.mark.parametrize("env_name,env", _ENV_MATRIX)
@pytest.mark.parametrize("name,make_inputs", REQUEST_SET)
def test_project_fathom_matches_fathom_properties(
    name, make_inputs, env_name, env, monkeypatch
):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    expected, got = _fathom_pair(make_inputs())
    assert _strip_ts(got) == _strip_ts(expected)


def test_enrich_order_independent_for_gemini():
    """The Gemini enrich is idempotent: building the event before vs after the old
    builder yields the same enterprise projection (pinned per spec)."""
    inputs = _gemini_success()
    kwargs, resp, start, end, success = inputs

    # event built FIRST, then old builder on a fresh copy
    ev_first = build_request_event(
        copy.deepcopy(kwargs), resp, start, end, success=success
    )
    rec_after = AirlockLogger._build_record(
        copy.deepcopy(kwargs), resp, start, end, success=success
    )
    assert _strip_ts(project_enterprise(ev_first)) == _strip_ts(rec_after)
