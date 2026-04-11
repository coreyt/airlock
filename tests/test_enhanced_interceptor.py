import pytest

from airlock.guardrails.enhanced_interceptor import EnhancedModelInterceptor


@pytest.fixture
def interceptor() -> EnhancedModelInterceptor:
    return EnhancedModelInterceptor()


@pytest.mark.asyncio
async def test_no_enhanced_profile(interceptor: EnhancedModelInterceptor) -> None:
    data = {"model": "regular-model", "litellm_params": {"other": "value"}}
    result = await interceptor.async_pre_call(data)
    assert result["model"] == "regular-model"


@pytest.mark.asyncio
async def test_missing_target_model(
    interceptor: EnhancedModelInterceptor, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    data = {
        "model": "enhanced-missing",
        "litellm_params": {"enhanced_profile": {"system_prompt": "test"}},
    }
    with caplog.at_level(logging.WARNING):
        result = await interceptor.async_pre_call(data)
    assert result == data
    assert "is missing 'target_model'" in caplog.text


@pytest.mark.asyncio
async def test_target_model_mutation(interceptor: EnhancedModelInterceptor) -> None:
    data = {
        "model": "logical-model",
        "litellm_params": {"enhanced_profile": {"target_model": "physical-model"}},
    }
    result = await interceptor.async_pre_call(data)
    assert result["model"] == "physical-model"


@pytest.mark.asyncio
async def test_inject_system_prompt_empty_messages(
    interceptor: EnhancedModelInterceptor,
) -> None:
    data = {
        "model": "logical-model",
        "messages": [],
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "system_prompt": "New system prompt",
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    assert result["messages"][0] == {"role": "system", "content": "New system prompt"}


@pytest.mark.asyncio
async def test_inject_system_prompt_existing_system_message(
    interceptor: EnhancedModelInterceptor,
) -> None:
    data = {
        "model": "logical-model",
        "messages": [{"role": "system", "content": "Original prompt"}],
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "system_prompt": "Appended prompt",
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    assert result["messages"][0]["content"] == "Original prompt\n\nAppended prompt"


@pytest.mark.asyncio
async def test_inject_system_prompt_existing_user_message(
    interceptor: EnhancedModelInterceptor,
) -> None:
    data = {
        "model": "logical-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "system_prompt": "Injected prompt",
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    assert result["messages"][0] == {"role": "system", "content": "Injected prompt"}
    assert result["messages"][1] == {"role": "user", "content": "Hello"}


@pytest.mark.asyncio
async def test_merge_optional_params(interceptor: EnhancedModelInterceptor) -> None:
    data = {
        "model": "logical-model",
        "optional_params": {"existing": 1},
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "params": {"thinking": True, "temperature": 0.5},
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    assert result["optional_params"]["existing"] == 1
    assert result["optional_params"]["thinking"] is True
    assert result["optional_params"]["temperature"] == 0.5
    assert result["model"] == "physical-model"


@pytest.mark.asyncio
async def test_inject_system_prompt_empty_content(
    interceptor: EnhancedModelInterceptor,
) -> None:
    data = {
        "model": "logical-model",
        "messages": [{"role": "system", "content": ""}],
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "system_prompt": "New system prompt",
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    assert result["messages"][0] == {"role": "system", "content": "New system prompt"}


@pytest.mark.asyncio
async def test_none_messages(interceptor: EnhancedModelInterceptor) -> None:
    data = {
        "model": "logical-model",
        "messages": None,
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "system_prompt": "New system prompt",
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    assert result["messages"][0] == {"role": "system", "content": "New system prompt"}


@pytest.mark.asyncio
async def test_none_optional_params(interceptor: EnhancedModelInterceptor) -> None:
    data = {
        "model": "logical-model",
        "optional_params": None,
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "params": {"temperature": 0.5},
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    assert result["optional_params"]["temperature"] == 0.5
