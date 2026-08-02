from __future__ import annotations

import json

import pytest

from airlock.fast.guardian import AirlockFastGuardian
from airlock.fast.model_alias import AliasResolution, CrossTierFuzzyMatch
from airlock.proxy_errors import (
    AirlockInvalidReasoningEffort,
    AirlockModelNotFound,
    airlock_model_not_found_handler,
    model_not_found_response_payload,
)


@pytest.mark.asyncio
async def test_guardian_rejects_known_invalid_reasoning_effort_before_dispatch(
    fresh_state_store, mock_cache, mock_user_api_key_dict, monkeypatch
) -> None:
    """P-2 runs after final routing and before LiteLLM can drop the value."""
    import airlock.fast.guardian as guardian_module

    seen = {}

    def _reject(data, provider, *, client_id):
        seen.update(provider=provider, client_id=client_id, value=data["reasoning_effort"])
        raise AirlockInvalidReasoningEffort(
            "none", "gpt-5.4", frozenset({"minimal", "low", "medium", "high"})
        )

    monkeypatch.setattr(
        guardian_module.alias_table,
        "resolve_with_diagnostic",
        lambda _: AliasResolution(alias="gpt-5.4"),
    )
    monkeypatch.setattr(guardian_module, "validate_reasoning_effort", _reject)
    data = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "gpt-5.4",
        "reasoning_effort": "none",
    }

    with pytest.raises(AirlockInvalidReasoningEffort):
        await AirlockFastGuardian().async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

    assert seen["provider"] == "openai"
    assert seen["value"] == "none"


@pytest.mark.asyncio
async def test_refused_model_returns_ranked_404_suggestions(
    fresh_state_store, mock_cache, mock_user_api_key_dict, monkeypatch
) -> None:
    import airlock.fast.guardian as guardian_module

    suggestions = [
        {"model": "gpt-5.6-luna", "score": 0.91, "tier": "standard"},
        {"model": "gpt-5.6-sol", "score": 0.73, "tier": "premium"},
    ]
    monkeypatch.setattr(
        guardian_module.alias_table,
        "resolve_with_diagnostic",
        lambda _: AliasResolution(alias=None),
    )
    monkeypatch.setattr(
        guardian_module.alias_table, "suggest", lambda *args, **kwargs: suggestions
    )

    with pytest.raises(AirlockModelNotFound) as raised:
        await AirlockFastGuardian().async_pre_call_hook(
            mock_user_api_key_dict,
            mock_cache,
            {"messages": [{"role": "user", "content": "hi"}], "model": "gpt-5.6-mini"},
            "completion",
        )

    body, headers = model_not_found_response_payload(raised.value)
    error = body["error"]
    assert "gpt-5.6-mini" in error["message"]
    assert "gpt-5.6-luna" in error["message"]
    assert error["code"] == "model_not_found"
    assert error["airlock"]["suggestions"] == suggestions
    assert headers["X-Airlock-Model-Suggestion"] == (
        "requested=gpt-5.6-mini;suggested=gpt-5.6-luna;reason=dropped_qualifier"
    )

    response = await airlock_model_not_found_handler(None, raised.value)
    assert response.status_code == 404
    assert json.loads(response.body) == body


@pytest.mark.asyncio
async def test_genuinely_unknown_model_is_not_given_empty_suggestions(
    fresh_state_store, mock_cache, mock_user_api_key_dict, monkeypatch
) -> None:
    import airlock.fast.guardian as guardian_module

    monkeypatch.setattr(
        guardian_module.alias_table,
        "resolve_with_diagnostic",
        lambda _: AliasResolution(alias=None),
    )
    monkeypatch.setattr(
        guardian_module.alias_table, "suggest", lambda *args, **kwargs: []
    )
    data = {"messages": [{"role": "user", "content": "hi"}], "model": "not-a-model"}

    result = await AirlockFastGuardian().async_pre_call_hook(
        mock_user_api_key_dict, mock_cache, data, "completion"
    )

    assert result["model"] == "not-a-model"


@pytest.mark.asyncio
async def test_cross_tier_fuzzy_match_returns_404_with_suggested_alternate(
    fresh_state_store, mock_cache, mock_user_api_key_dict, monkeypatch, caplog
) -> None:
    """P-2b refuses a costly ambiguous fuzzy route with the existing 404 shape."""
    import airlock.fast.guardian as guardian_module

    detail = CrossTierFuzzyMatch(
        served="gpt-alpha-1",
        suggested="gpt-alpha-2",
        score=0.75,
        from_tier="low",
        to_tier="high",
    )
    monkeypatch.setattr(
        guardian_module.alias_table,
        "resolve_with_diagnostic",
        lambda _: AliasResolution(alias="gpt-alpha-1", cross_tier=detail),
    )
    monkeypatch.setattr(
        guardian_module.alias_table,
        "suggest",
        lambda _: [
            {"model": "gpt-alpha-1", "score": 0.81, "tier": "low"},
            {"model": "gpt-alpha-2", "score": 0.75, "tier": "high"},
        ],
    )
    data = {"messages": [{"role": "user", "content": "hi"}], "model": "gpt-alpha"}

    with caplog.at_level("WARNING", logger="airlock.fast.guardian"):
        with pytest.raises(AirlockModelNotFound) as raised:
            await AirlockFastGuardian().async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    body, headers = model_not_found_response_payload(raised.value)
    assert body["error"]["code"] == "model_not_found"
    assert body["error"]["airlock"]["suggestions"][0]["model"] == "gpt-alpha-2"
    assert headers["X-Airlock-Model-Suggestion"] == (
        "requested=gpt-alpha;suggested=gpt-alpha-2;reason=fuzzy_match_crosses_cost_tier"
    )
    assert "event=fuzzy_match_rejected" in caplog.text
    assert "client_id=" in caplog.text
