"""Golden equivalence tests for Pack 0.5.4-MIGRATE-entfathom-project/cutover.

Prove the PURE projections in ``airlock.callbacks.projections`` reproduce the
historical builder output field-for-field:

* ``project_enterprise(event)`` == frozen ``AirlockLogger._build_record(...)`` golden
* ``project_fathom(event)``     == frozen ``_fathom_properties(...)`` golden

…ignoring ONLY ``timestamp`` (the one registered convergence, design §3.4),
across a representative request set + the 7-flag ``AIRLOCK_FATHOM_STORE_*``
matrix. No network: inputs are built in-process.

ORACLE FREEZE (pack 2b-ii cutover): the goldens in
``tests/fixtures/0.5.4-entfathom-golden.json`` were captured ONCE from the live
``_build_record``/``_fathom_properties`` builders (verified exact) and the builders
were then DELETED. The equivalence proof therefore no longer depends on the live
builders — it pins the projections to the captured historical shape. ``_FakeResponse``
carries a deterministic ``__repr__`` so ``_serialize(resp)`` is process-stable and
freezable; both sides normalize datetimes via ``json default=_serialize``.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from airlock.callbacks.enterprise_logger import _serialize
from airlock.callbacks.projections import project_enterprise, project_fathom, project_s3
from airlock.callbacks.request_event import build_request_event
from airlock.transparency import Mutation

_GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "0.5.4-entfathom-golden.json").read_text()
)

_S3_GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "0.5.4-s3-golden.json").read_text()
)


def _jsonify(record: dict) -> dict:
    """Strip ``timestamp`` and JSON-normalize (datetimes -> isoformat) for comparison
    against the frozen goldens, exactly as the goldens were captured."""
    record = dict(record)
    record.pop("timestamp", None)
    return json.loads(json.dumps(record, default=_serialize))


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
        self._tag = (
            f"prompt={prompt} completion={completion} total={total} content={content!r}"
        )

    def __repr__(self) -> str:
        # Deterministic (no memory address) so ``_serialize(resp) == str(resp)`` is
        # stable across processes — required to FREEZE the projection goldens.
        return f"_FakeResponse({self._tag})"


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


def _guardrail_collision():
    # airlock_* keys that COLLIDE with base record keys: `airlock_provider` is a
    # base literal (the `**guardrail_meta` spread must not move/overwrite it with a
    # different value), and `airlock_client` is assigned AFTER the spread (the
    # post-literal assignment order is observable here).
    k = _kwargs(
        metadata={
            "airlock_provider": "openai",
            "airlock_client": "client-from-meta",
            "airlock_semantic_score": 0.5,
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
    ("guardrail_collision", _guardrail_collision),
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


def _enterprise_projection(inputs):
    kwargs, resp, start, end, success = inputs
    event = build_request_event(kwargs, resp, start, end, success=success)
    return project_enterprise(event)


def _fathom_projection(inputs):
    kwargs, resp, start, end, success = inputs
    event = build_request_event(kwargs, resp, start, end, success=success)
    return project_fathom(event)


def _s3_projection(inputs):
    kwargs, resp, start, end, success = inputs
    event = build_request_event(kwargs, resp, start, end, success=success)
    return project_s3(event)


@pytest.mark.parametrize("name,make_inputs", REQUEST_SET)
def test_project_enterprise_matches_build_record(name, make_inputs):
    got = _enterprise_projection(make_inputs())
    # timestamp is the ONLY accepted divergence (registered convergence §3.4)
    assert got.get("timestamp") is not None
    expected = _GOLDEN["enterprise"][name]
    got_no_ts = _jsonify(got)
    assert got_no_ts == expected
    # key ORDER must match too (a reorder-only regression must not pass — the
    # cutover deletes _build_record and relies on this projection's exact shape)
    assert list(got_no_ts.keys()) == list(expected.keys())


@pytest.mark.parametrize("env_name,env", _ENV_MATRIX)
@pytest.mark.parametrize("name,make_inputs", REQUEST_SET)
def test_project_fathom_matches_fathom_properties(
    name, make_inputs, env_name, env, monkeypatch
):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    got = _fathom_projection(make_inputs())
    expected = _GOLDEN["fathom"][f"{name}::{env_name}"]
    got_no_ts = _jsonify(got)
    assert got_no_ts == expected
    assert list(got_no_ts.keys()) == list(expected.keys())


@pytest.mark.parametrize("name,make_inputs", REQUEST_SET)
def test_project_s3_matches_build_record(name, make_inputs):
    """``project_s3`` reproduces the FROZEN ``AirlockS3Logger._build_record`` golden
    field-for-field, ignoring only ``timestamp`` (s3's narrow set: no guardrail/mcp/
    served/provider/record_type)."""
    got = _s3_projection(make_inputs())
    assert got.get("timestamp") is not None
    expected = _S3_GOLDEN["s3"][name]
    got_no_ts = _jsonify(got)
    assert got_no_ts == expected
    # key ORDER must match too (the cutover deletes _build_record and relies on
    # this projection's exact shape)
    assert list(got_no_ts.keys()) == list(expected.keys())


def test_project_s3_redaction_matches_build_record(monkeypatch):
    """s3's ``_redact_record`` pass is reproduced: AIRLOCK_LOG_REDACT_FIELDS-targeted
    fields are replaced with ``[REDACTED]`` identically to the old builder."""
    monkeypatch.setenv("AIRLOCK_LOG_REDACT_FIELDS", "messages,model")
    got = _s3_projection(_plain_success())
    expected = _S3_GOLDEN["s3_redacted"]["plain_success"]
    assert _jsonify(got) == expected
    assert got["messages"] == "[REDACTED]"
    assert got["model"] == "[REDACTED]"


def test_enrich_order_independent_for_gemini():
    """The Gemini enrich is idempotent: the enterprise projection of the event
    reproduces the frozen gemini golden (pinned per spec)."""
    kwargs, resp, start, end, success = _gemini_success()
    ev = build_request_event(kwargs, resp, start, end, success=success)
    assert _jsonify(project_enterprise(ev)) == _GOLDEN["enterprise"]["gemini_success"]


@pytest.mark.parametrize(
    "cost_case,expect_present,expect_value",
    [
        # absent: old kwargs.get("response_cost", 0) == 0 -> kept (0 is not None)
        ("absent", True, 0),
        # explicit None: old -> None -> dropped by the final None-filter
        ("none", False, None),
        # numeric: value passes through
        ("numeric", True, 0.0042),
    ],
)
def test_project_fathom_cost_cases(cost_case, expect_present, expect_value):
    """Fathom `cost` must reproduce `kwargs.get("response_cost", 0)` for all three
    cases — the F1 keystone fix (source default 0 + raw projection + None-filter)."""
    k = _kwargs()
    if cost_case == "absent":
        k.pop("response_cost", None)
    elif cost_case == "none":
        k["response_cost"] = None
    else:
        k["response_cost"] = 0.0042

    got = _fathom_projection((k, _FakeResponse(), _ts(0), _ts(1), True))
    expected = _GOLDEN["fathom_cost"][cost_case]
    assert _jsonify(got) == expected

    if expect_present:
        assert got["cost"] == expect_value
        assert expected["cost"] == expect_value
    else:
        assert "cost" not in got
        assert "cost" not in expected
