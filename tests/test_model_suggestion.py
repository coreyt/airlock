from __future__ import annotations

import json

import pytest

from airlock.fast.guardian import AirlockFastGuardian
from airlock.fast.model_alias import AliasResolution, CrossTierFuzzyMatch
from airlock.proxy_errors import (
    AirlockModelNotFound,
    airlock_model_not_found_handler,
    model_not_found_response_payload,
)


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
async def test_cross_tier_fuzzy_match_is_warn_only_and_ledgered(
    fresh_state_store, mock_cache, mock_user_api_key_dict, monkeypatch, caplog
) -> None:
    """P-2b instrumentation changes neither the alias nor the response path."""
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
    data = {"messages": [{"role": "user", "content": "hi"}], "model": "gpt-alpha"}

    with caplog.at_level("WARNING", logger="airlock.fast.guardian"):
        result = await AirlockFastGuardian().async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

    assert result["model"] == "gpt-alpha-1"
    assert "event=fuzzy_match_would_reject" in caplog.text
    assert "client_id=" in caplog.text
    mutation = next(
        m
        for m in result["metadata"]["airlock_mutations"]
        if m.field == "model_alias_would_reject"
    )
    assert mutation.op == "inject"
    assert mutation.source == "guardian.alias_cross_tier_measurement"
