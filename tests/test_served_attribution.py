"""attribute_served_backend — served-backend identity read from _hidden_params."""

from __future__ import annotations

from types import SimpleNamespace

from airlock.transparency import (
    ServedBackend,
    attribute_served_backend,
    served_headers,
)


def _resp(hidden: dict | None = None, **attrs):
    return SimpleNamespace(_hidden_params=hidden or {}, **attrs)


def test_anthropic_native() -> None:
    s = attribute_served_backend(
        _resp({"custom_llm_provider": "anthropic", "response_cost": 0.01})
    )
    assert isinstance(s, ServedBackend)
    assert s.provider == "anthropic"
    assert s.backend_kind == "native"
    assert s.response_cost == 0.01


def test_openai_native() -> None:
    s = attribute_served_backend(_resp({"custom_llm_provider": "openai"}))
    assert s.provider == "openai"
    assert s.backend_kind == "native"


def test_bedrock_gateway_with_region_and_model_id() -> None:
    s = attribute_served_backend(
        _resp(
            {
                "custom_llm_provider": "bedrock",
                "api_base": "https://bedrock-runtime.us-east-1.amazonaws.com/x",
                "region_name": "us-east-1",
                "model_id": "anthropic.claude-3",
            }
        )
    )
    assert s.provider == "bedrock"
    assert s.backend_kind == "gateway"
    assert s.region == "us-east-1"
    assert s.model_id == "anthropic.claude-3"
    assert s.api_base_host == "bedrock-runtime.us-east-1.amazonaws.com"


def test_azure_gateway() -> None:
    s = attribute_served_backend(_resp({"custom_llm_provider": "azure"}))
    assert s.backend_kind == "gateway"


def test_vertex_gateway() -> None:
    s = attribute_served_backend(
        _resp(
            {
                "custom_llm_provider": "vertex_ai",
                "region_name": "us-east5",
                "api_base": "https://us-east5-aiplatform.googleapis.com/v1",
            }
        )
    )
    assert s.provider == "vertex_ai"
    assert s.backend_kind == "gateway"
    assert s.region == "us-east5"


def test_gemini_ai_studio_native_distinct_from_vertex() -> None:
    s = attribute_served_backend(
        _resp(
            {
                "custom_llm_provider": "gemini",
                "api_base": "https://generativelanguage.googleapis.com/v1beta",
            }
        )
    )
    assert s.provider == "gemini"
    assert s.backend_kind == "native"
    assert s.api_base_host == "generativelanguage.googleapis.com"


def test_model_id_fallback_chain() -> None:
    s = attribute_served_backend(
        _resp({"custom_llm_provider": "openai", "litellm_model_name": "gpt-4o-2024"})
    )
    assert s.model_id == "gpt-4o-2024"
    s2 = attribute_served_backend(
        _resp({"custom_llm_provider": "openai", "received_model_id": "got-it"})
    )
    assert s2.model_id == "got-it"


def test_streaming_provider_from_wrapper_attribute() -> None:
    # _hidden_params lacks custom_llm_provider; wrapper attribute carries it.
    s = attribute_served_backend(
        _resp({"model_id": "m"}, custom_llm_provider="anthropic")
    )
    assert s.provider == "anthropic"
    assert s.backend_kind == "native"


def test_unknown_provider_yields_partial_and_empty_headers() -> None:
    s = attribute_served_backend(_resp({}))
    assert s is not None
    assert s.provider is None
    assert s.backend_kind == "unknown"
    assert served_headers(s) == {}


def test_cost_fallback_used_when_absent() -> None:
    s = attribute_served_backend(
        _resp({"custom_llm_provider": "openai"}), cost_fallback=0.05
    )
    assert s.response_cost == 0.05
    # present cost wins over fallback
    s2 = attribute_served_backend(
        _resp({"custom_llm_provider": "openai", "response_cost": 0.09}),
        cost_fallback=0.05,
    )
    assert s2.response_cost == 0.09


def test_falsy_response_returns_none() -> None:
    assert attribute_served_backend(None) is None


def test_served_headers_includes_region_only_when_present() -> None:
    s = ServedBackend(
        provider="vertex_ai",
        api_base_host="h",
        region="us-east5",
        model_id="m",
        response_cost=None,
        backend_kind="gateway",
    )
    h = served_headers(s)
    assert h["X-Airlock-Served-By"] == "vertex_ai"
    assert h["X-Airlock-Served-Region"] == "us-east5"

    s_no_region = ServedBackend(
        provider="openai",
        api_base_host=None,
        region=None,
        model_id=None,
        response_cost=None,
        backend_kind="native",
    )
    h2 = served_headers(s_no_region)
    assert h2 == {"X-Airlock-Served-By": "openai"}


def test_served_headers_none_input() -> None:
    assert served_headers(None) == {}


# ---------------------------------------------------------------------------
# CR/LF header-injection safety tests for served_headers
# ---------------------------------------------------------------------------


def test_served_headers_crlf_in_provider_is_stripped() -> None:
    """provider value with CR/LF cannot inject a new header line.

    After stripping, the CRLF is gone so no new header can be injected;
    any remaining text is embedded in the same value — that is fine.
    """
    s = ServedBackend(
        provider="anthropic\r\nX-Injected: evil",
        api_base_host=None,
        region=None,
        model_id=None,
        response_cost=None,
        backend_kind="native",
    )
    h = served_headers(s)
    val = h.get("X-Airlock-Served-By", "")
    assert "\r" not in val
    assert "\n" not in val


def test_served_headers_crlf_in_region_is_stripped() -> None:
    """region value with CR/LF cannot inject a new header line."""
    s = ServedBackend(
        provider="bedrock",
        api_base_host=None,
        region="us-east-1\r\nX-Injected: evil",
        model_id=None,
        response_cost=None,
        backend_kind="gateway",
    )
    h = served_headers(s)
    val = h.get("X-Airlock-Served-Region", "")
    assert "\r" not in val
    assert "\n" not in val
