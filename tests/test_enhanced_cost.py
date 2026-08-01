from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from airlock.fast.monitor import AirlockFastMonitor
from airlock.fast.state import StateStore, set_store, store
from airlock.litellm_adapter import response_cost
from airlock.providers.enhanced_passthrough import EnhancedPassthroughProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("inner_cost", [0.0032, 0.0512])
async def test_enhanced_inner_cost_survives_and_records_provider_spend(
    inner_cost: float,
) -> None:
    """Target-model pricing, including long-context pricing, reaches the sink."""
    response = SimpleNamespace(
        _hidden_params={
            "custom_llm_provider": "gemini",
            "response_cost": inner_cost,
            "model_id": "gemini-3.1-pro-preview-customtools",
        }
    )
    provider = EnhancedPassthroughProvider()

    with patch(
        "airlock.providers.enhanced_passthrough.litellm.acompletion",
        new=AsyncMock(return_value=response),
    ):
        result = await provider.acompletion(
            model="enhanced/gemini-coding",
            messages=[{"role": "user", "content": "hi"}],
            api_base="",
            custom_prompt_dict={},
            model_response=SimpleNamespace(),
            print_verbose=lambda *args, **kwargs: None,
            encoding=None,
            api_key=None,
            logging_obj=None,
            optional_params={},
            litellm_params={
                "enhanced_profile": {
                    "target_model": "gemini/gemini-3.1-pro-preview-customtools"
                }
            },
        )

    assert result is response
    assert response_cost(result) == inner_cost

    set_store(StateStore())
    try:
        start = datetime.now()
        AirlockFastMonitor().log_success_event(
            {
                "model": "gemini-coding",
                "response_cost": 0.0,
                "litellm_params": {"metadata": {"airlock_client": "f4-test"}},
            },
            result,
            start,
            start + timedelta(milliseconds=1),
        )
        assert store.get_provider_spend("gemini").recent_spend() == pytest.approx(
            inner_cost
        )
    finally:
        set_store(None)
