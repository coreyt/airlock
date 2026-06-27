"""ACL (litellm_adapter) unit + byte-parity tests (Pack 0.5.3-ACL).

The adapter is a PURE EXTRACTION: every accessor must return exactly what the
pre-extraction inline reads returned, and the served-backend attribution +
headers must be byte-identical to what ``transparency.attribute_served_backend``
produces. The parity fixtures capture the CURRENT behavior as literal expected
values (the whole point is no change).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from airlock import litellm_adapter as acl
from airlock.transparency import attribute_served_backend, served_headers


def _resp(hidden: dict | None = None, **attrs):
    return SimpleNamespace(_hidden_params=hidden or {}, **attrs)


# ---------------------------------------------------------------------------
# hidden_params
# ---------------------------------------------------------------------------
def test_hidden_params_returns_dict() -> None:
    hp = {"custom_llm_provider": "openai", "response_cost": 0.01}
    assert acl.hidden_params(_resp(hp)) == hp


def test_hidden_params_missing_attr_returns_none() -> None:
    assert acl.hidden_params(object()) is None


def test_hidden_params_none_value() -> None:
    assert acl.hidden_params(SimpleNamespace(_hidden_params=None)) is None


# ---------------------------------------------------------------------------
# served_provider
# ---------------------------------------------------------------------------
def test_served_provider_from_hidden_params() -> None:
    assert acl.served_provider(_resp({"custom_llm_provider": "anthropic"})) == "anthropic"


def test_served_provider_falls_back_to_wrapper_attribute() -> None:
    # streaming: _hidden_params lacks the provider; wrapper attribute carries it.
    r = _resp({"model_id": "m"}, custom_llm_provider="vertex_ai_beta")
    assert acl.served_provider(r) == "vertex_ai_beta"


def test_served_provider_none_when_absent() -> None:
    assert acl.served_provider(_resp({})) is None


# ---------------------------------------------------------------------------
# response_cost
# ---------------------------------------------------------------------------
def test_response_cost_from_hidden_params() -> None:
    assert acl.response_cost(_resp({"response_cost": 0.07})) == 0.07


def test_response_cost_fallback_when_absent() -> None:
    assert acl.response_cost(_resp({}), fallback=0.05) == 0.05


def test_response_cost_present_wins_over_fallback() -> None:
    assert acl.response_cost(_resp({"response_cost": 0.09}), fallback=0.05) == 0.09


def test_response_cost_default_fallback_none() -> None:
    assert acl.response_cost(_resp({})) is None


# ---------------------------------------------------------------------------
# additional_headers (read) + merge_additional_headers (write)
# ---------------------------------------------------------------------------
def test_additional_headers_read() -> None:
    r = _resp({"additional_headers": {"x-ratelimit-remaining-tokens": "10"}})
    assert acl.additional_headers(r) == {"x-ratelimit-remaining-tokens": "10"}


def test_additional_headers_absent_returns_none() -> None:
    assert acl.additional_headers(_resp({})) is None


def test_additional_headers_no_hidden_params_returns_none() -> None:
    assert acl.additional_headers(object()) is None


def test_merge_additional_headers_writes_into_hidden_params() -> None:
    hp: dict = {}
    r = _resp(hp)
    ok = acl.merge_additional_headers(r, {"X-Airlock-Served-By": "openai"})
    assert ok is True
    assert hp["additional_headers"] == {"X-Airlock-Served-By": "openai"}


def test_merge_additional_headers_updates_existing() -> None:
    hp: dict = {"additional_headers": {"a": "1"}}
    r = _resp(hp)
    acl.merge_additional_headers(r, {"b": "2"})
    assert hp["additional_headers"] == {"a": "1", "b": "2"}


def test_merge_additional_headers_no_dict_hidden_params_is_noop() -> None:
    r = SimpleNamespace(_hidden_params=None)
    assert acl.merge_additional_headers(r, {"a": "1"}) is False


# ---------------------------------------------------------------------------
# resolve_proxy_app
# ---------------------------------------------------------------------------
def test_resolve_proxy_app_none_when_module_absent() -> None:
    saved = sys.modules.pop("litellm.proxy.proxy_server", None)
    try:
        assert acl.resolve_proxy_app() is None
    finally:
        if saved is not None:
            sys.modules["litellm.proxy.proxy_server"] = saved


def test_resolve_proxy_app_reads_app_attr() -> None:
    sentinel = object()
    fake = SimpleNamespace(app=sentinel)
    saved = sys.modules.get("litellm.proxy.proxy_server")
    sys.modules["litellm.proxy.proxy_server"] = fake  # type: ignore[assignment]
    try:
        assert acl.resolve_proxy_app() is sentinel
    finally:
        if saved is not None:
            sys.modules["litellm.proxy.proxy_server"] = saved
        else:
            del sys.modules["litellm.proxy.proxy_server"]


# ---------------------------------------------------------------------------
# install_asgi_middleware — None vs built-stack branch
# ---------------------------------------------------------------------------
class _FakeApp:
    def __init__(self, *, started: bool) -> None:
        self.middleware_stack = object() if started else None
        self.added: list = []

    def add_middleware(self, cls, *args, **kwargs) -> None:
        self.added.append((cls, args, kwargs))


class _Wrapper:
    def __init__(self, app, *args, **kwargs) -> None:
        self.app = app
        self.args = args
        self.kwargs = kwargs


def test_install_asgi_middleware_not_started_uses_add_middleware() -> None:
    app = _FakeApp(started=False)
    acl.install_asgi_middleware(app, _Wrapper, capability_map={"m": {}})
    assert app.added == [(_Wrapper, (), {"capability_map": {"m": {}}})]
    assert app.middleware_stack is None


def test_install_asgi_middleware_started_wraps_built_stack() -> None:
    app = _FakeApp(started=True)
    original_stack = app.middleware_stack
    acl.install_asgi_middleware(app, _Wrapper, capability_map={"m": {}})
    assert isinstance(app.middleware_stack, _Wrapper)
    assert app.middleware_stack.app is original_stack
    assert app.middleware_stack.kwargs == {"capability_map": {"m": {}}}
    assert app.added == []


def test_install_asgi_middleware_no_extra_args() -> None:
    app = _FakeApp(started=False)
    acl.install_asgi_middleware(app, _Wrapper)
    assert app.added == [(_Wrapper, (), {})]


# ---------------------------------------------------------------------------
# Byte-parity / characterization (AC-ACL): served-backend attribution + headers
# must be identical before vs after. Expected values are captured literals.
# ---------------------------------------------------------------------------
PARITY_CASES = {
    # 1. Anthropic vs Bedrock vs Vertex (same-ish model, different provider)
    "anthropic_native": (
        _resp({"custom_llm_provider": "anthropic", "response_cost": 0.01}),
        None,
        {
            "provider": "anthropic",
            "backend_kind": "native",
            "region": None,
            "api_base_host": None,
        },
        {"X-Airlock-Served-By": "anthropic"},
    ),
    "bedrock_gateway": (
        _resp(
            {
                "custom_llm_provider": "bedrock",
                "api_base": "https://bedrock-runtime.us-east-1.amazonaws.com/x",
                "region_name": "us-east-1",
                "model_id": "anthropic.claude-3",
            }
        ),
        None,
        {
            "provider": "bedrock",
            "backend_kind": "gateway",
            "region": "us-east-1",
            "api_base_host": "bedrock-runtime.us-east-1.amazonaws.com",
        },
        {
            "X-Airlock-Served-By": "bedrock",
            "X-Airlock-Served-Region": "us-east-1",
        },
    ),
    "vertex_gateway": (
        _resp(
            {
                "custom_llm_provider": "vertex_ai",
                "region_name": "us-east5",
                "api_base": "https://us-east5-aiplatform.googleapis.com/v1",
            }
        ),
        None,
        {
            "provider": "vertex_ai",
            "backend_kind": "gateway",
            "region": "us-east5",
            "api_base_host": "us-east5-aiplatform.googleapis.com",
        },
        {
            "X-Airlock-Served-By": "vertex_ai",
            "X-Airlock-Served-Region": "us-east5",
        },
    ),
    # 2. OpenAI vs Azure
    "openai_native": (
        _resp({"custom_llm_provider": "openai"}),
        None,
        {
            "provider": "openai",
            "backend_kind": "native",
            "region": None,
            "api_base_host": None,
        },
        {"X-Airlock-Served-By": "openai"},
    ),
    "azure_gateway": (
        _resp({"custom_llm_provider": "azure"}),
        None,
        {
            "provider": "azure",
            "backend_kind": "gateway",
            "region": None,
            "api_base_host": None,
        },
        {"X-Airlock-Served-By": "azure"},
    ),
    # 3. Vertex vs Gemini (AI Studio) split by api_base host
    "gemini_ai_studio": (
        _resp(
            {
                "custom_llm_provider": "gemini",
                "api_base": "https://generativelanguage.googleapis.com/v1beta",
            }
        ),
        None,
        {
            "provider": "gemini",
            "backend_kind": "native",
            "region": None,
            "api_base_host": "generativelanguage.googleapis.com",
        },
        {"X-Airlock-Served-By": "gemini"},
    ),
    # 4a. streaming vertex_ai_beta hardcode + AI Studio host → gemini (native)
    "streaming_vertex_ai_beta_ai_studio": (
        _resp(
            {
                "model_id": "gemini-3.5-flash",
                "api_base": (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-3.5-flash:streamGenerateContent"
                ),
            },
            custom_llm_provider="vertex_ai_beta",
        ),
        None,
        {
            "provider": "gemini",
            "backend_kind": "native",
            "region": None,
            "api_base_host": "generativelanguage.googleapis.com",
        },
        {"X-Airlock-Served-By": "gemini"},
    ),
    # 4b. streaming vertex_ai_beta hardcode + Vertex host → vertex_ai (gateway)
    "streaming_vertex_ai_beta_vertex": (
        _resp(
            {
                "region_name": "us-east5",
                "api_base": (
                    "https://us-east5-aiplatform.googleapis.com/v1/projects/p/"
                    "locations/us-east5/publishers/google/models/g:streamGenerateContent"
                ),
            },
            custom_llm_provider="vertex_ai_beta",
        ),
        None,
        {
            "provider": "vertex_ai",
            "backend_kind": "gateway",
            "region": "us-east5",
            "api_base_host": "us-east5-aiplatform.googleapis.com",
        },
        {
            "X-Airlock-Served-By": "vertex_ai",
            "X-Airlock-Served-Region": "us-east5",
        },
    ),
    # 5. unknown provider ⇒ header omitted (not guessed)
    "unknown_provider": (
        _resp({}),
        None,
        {
            "provider": None,
            "backend_kind": "unknown",
            "region": None,
            "api_base_host": None,
        },
        {},
    ),
}


@pytest.mark.parametrize("name", sorted(PARITY_CASES))
def test_served_backend_byte_parity(name: str) -> None:
    response, cost_fallback, expected_fields, expected_headers = PARITY_CASES[name]
    served = attribute_served_backend(response, cost_fallback=cost_fallback)
    assert served is not None
    for field, value in expected_fields.items():
        assert getattr(served, field) == value, f"{name}.{field}"
    assert served_headers(served) == expected_headers, f"{name}.headers"


def test_unknown_provider_header_omitted_not_guessed() -> None:
    served = attribute_served_backend(_resp({"api_base": "https://x.example/v1"}))
    assert served is not None
    assert served.provider is None
    assert served_headers(served) == {}
