from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from airlock.providers.enhanced_passthrough import EnhancedPassthroughProvider


@pytest.fixture
def provider() -> EnhancedPassthroughProvider:
    return EnhancedPassthroughProvider()


def test_completion_forwards_to_target_model(provider: EnhancedPassthroughProvider) -> None:
    response = MagicMock()
    with patch("airlock.providers.enhanced_passthrough.litellm.completion", return_value=response) as mock_completion:
        result = provider.completion(
            model="enhanced/gemini-coding",
            messages=[{"role": "user", "content": "hi"}],
            api_base="",
            custom_prompt_dict={},
            model_response=MagicMock(),
            print_verbose=lambda *args, **kwargs: None,
            encoding=None,
            api_key=None,
            logging_obj=None,
            optional_params={"temperature": 0.1},
            litellm_params={
                "enhanced_profile": {
                    "target_model": "gemini/gemini-3.1-pro-preview-customtools",
                    "system_prompt": "sys",
                    "params": {"thinking": True, "thinking_level": "MEDIUM"},
                }
            },
        )

    assert result is response
    mock_completion.assert_called_once()
    kwargs = mock_completion.call_args.kwargs
    assert kwargs["model"] == "gemini/gemini-3.1-pro-preview-customtools"
    assert kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert kwargs["messages"][1] == {"role": "user", "content": "hi"}
    assert kwargs["temperature"] == 0.1
    assert kwargs["reasoning_effort"] == "medium"
    assert "thinking" not in kwargs
    assert "thinking_level" not in kwargs
    assert kwargs["no_log"] is True
    assert kwargs["metadata"]["airlock_skip_fathom_logger"] is True


@pytest.mark.asyncio
async def test_acompletion_forwards_to_target_model(
    provider: EnhancedPassthroughProvider,
) -> None:
    response = MagicMock()
    with patch(
        "airlock.providers.enhanced_passthrough.litellm.acompletion",
        new=AsyncMock(return_value=response),
    ) as mock_acompletion:
        result = await provider.acompletion(
            model="enhanced/gemini-coding",
            messages=[{"role": "system", "content": "orig"}, {"role": "user", "content": "hi"}],
            api_base="",
            custom_prompt_dict={},
            model_response=MagicMock(),
            print_verbose=lambda *args, **kwargs: None,
            encoding=None,
            api_key=None,
            logging_obj=None,
            optional_params={"custom_llm_provider": "enhanced"},
            litellm_params={
                "enhanced_profile": {
                    "target_model": "gemini/gemini-3.1-pro-preview-customtools",
                    "system_prompt": "extra",
                }
            },
        )

    assert result is response
    mock_acompletion.assert_awaited_once()
    kwargs = mock_acompletion.call_args.kwargs
    assert kwargs["model"] == "gemini/gemini-3.1-pro-preview-customtools"
    assert kwargs["messages"][0]["content"] == "orig\n\nextra"
    assert "custom_llm_provider" not in kwargs
    assert kwargs["no_log"] is True
    assert kwargs["metadata"]["airlock_skip_fathom_logger"] is True


def test_completion_requires_target_model(provider: EnhancedPassthroughProvider) -> None:
    with pytest.raises(ValueError, match="enhanced_profile.target_model"):
        provider.completion(
            model="enhanced/gemini-coding",
            messages=[{"role": "user", "content": "hi"}],
            api_base="",
            custom_prompt_dict={},
            model_response=MagicMock(),
            print_verbose=lambda *args, **kwargs: None,
            encoding=None,
            api_key=None,
            logging_obj=None,
            optional_params={},
            litellm_params={"enhanced_profile": {}},
        )


def test_completion_falls_back_to_config_profile(
    provider: EnhancedPassthroughProvider, tmp_path, monkeypatch
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        """
model_list:
  - model_name: gemini-coding
    litellm_params:
      model: enhanced/gemini-coding
      enhanced_profile:
        target_model: gemini/gemini-3.1-pro-preview-customtools
        system_prompt: config sys
        params:
          thinking: true
          thinking_level: MEDIUM
""".strip()
    )
    monkeypatch.setenv("AIRLOCK_CONFIG", str(config))

    response = MagicMock()
    with patch("airlock.providers.enhanced_passthrough.litellm.completion", return_value=response) as mock_completion:
        result = provider.completion(
            model="gemini-coding",
            messages=[{"role": "user", "content": "hi"}],
            api_base="",
            custom_prompt_dict={},
            model_response=MagicMock(),
            print_verbose=lambda *args, **kwargs: None,
            encoding=None,
            api_key=None,
            logging_obj=None,
            optional_params={},
            litellm_params={},
        )

    assert result is response
    kwargs = mock_completion.call_args.kwargs
    assert kwargs["model"] == "gemini/gemini-3.1-pro-preview-customtools"
    assert kwargs["messages"][0] == {"role": "system", "content": "config sys"}
    assert kwargs["reasoning_effort"] == "medium"
    assert kwargs["metadata"]["airlock_skip_fathom_logger"] is True
