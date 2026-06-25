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


@pytest.mark.asyncio
async def test_system_injection_records_inject(
    interceptor: EnhancedModelInterceptor,
) -> None:
    data = {
        "model": "logical-model",
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "system_prompt": "SECRET_SYS_PROMPT_TEXT",
                "name": "coder",
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    muts = result["metadata"]["airlock_mutations"]
    inj = [m for m in muts if m.op == "inject" and m.field == "system"]
    assert len(inj) == 1
    assert inj[0].source == "enhanced.interceptor"
    assert inj[0].stage == "pre_call"
    assert inj[0].before is None and inj[0].after is None
    # CC-T2: injected system-prompt text never lands in the ledger
    assert "SECRET_SYS_PROMPT_TEXT" not in repr(muts)
    # behavior unchanged
    assert result["model"] == "physical-model"


@pytest.mark.asyncio
async def test_no_system_prompt_no_inject_record(
    interceptor: EnhancedModelInterceptor,
) -> None:
    data = {
        "model": "logical-model",
        "litellm_params": {"enhanced_profile": {"target_model": "physical-model"}},
    }
    result = await interceptor.async_pre_call(data)
    muts = result.get("metadata", {}).get("airlock_mutations", [])
    assert [m for m in muts if m.op == "inject"] == []


_CONFIG_FALLBACK_YAML = """
model_list:
  - model_name: gemini-coding
    litellm_params:
      model: enhanced/gemini-coding
      enhanced_profile:
        target_model: gemini/gemini-3.1-pro-preview-customtools
        system_prompt: "CONFIG_SECRET_SYS_PROMPT_TEXT"
        params:
          thinking: true
          thinking_level: "MEDIUM"
  - model_name: plain-model
    litellm_params:
      model: gemini/gemini-3.1-pro-preview
""".strip()


@pytest.fixture
def config_fallback_env(tmp_path, monkeypatch):
    from airlock.providers.enhanced_passthrough import enhanced_handler

    config = tmp_path / "config.yaml"
    config.write_text(_CONFIG_FALLBACK_YAML)
    monkeypatch.setenv("AIRLOCK_CONFIG", str(config))
    # Force the shared provider cache to reload from this temp config.
    enhanced_handler._config_profile_cache = None
    enhanced_handler._config_profile_cache_key = None
    yield
    enhanced_handler._config_profile_cache = None
    enhanced_handler._config_profile_cache_key = None


@pytest.mark.asyncio
async def test_config_fallback_injection_records_inject(
    interceptor: EnhancedModelInterceptor, config_fallback_env
) -> None:
    # litellm_params lacks enhanced_profile; the provider resolves it from config.
    data = {
        "model": "gemini-coding",
        "messages": [{"role": "user", "content": "hi"}],
        "litellm_params": {"model": "enhanced/gemini-coding"},
    }
    result = await interceptor.async_pre_call(data)
    muts = result["metadata"]["airlock_mutations"]
    inj = [m for m in muts if m.op == "inject" and m.field == "system"]
    assert len(inj) == 1
    assert inj[0].source == "enhanced.passthrough"
    assert inj[0].stage == "pre_call"
    assert inj[0].before is None and inj[0].after is None
    # CC-T2: config system-prompt text never lands in the ledger
    assert "CONFIG_SECRET_SYS_PROMPT_TEXT" not in repr(muts)
    # behavior unchanged: the interceptor does NOT rewrite the model or messages
    # for the config-fallback path (the passthrough provider does that at exec).
    assert result["model"] == "gemini-coding"
    assert result["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_config_fallback_non_enhanced_no_record(
    interceptor: EnhancedModelInterceptor, config_fallback_env
) -> None:
    data = {
        "model": "plain-model",
        "messages": [{"role": "user", "content": "hi"}],
        "litellm_params": {"model": "gemini/gemini-3.1-pro-preview"},
    }
    result = await interceptor.async_pre_call(data)
    muts = result.get("metadata", {}).get("airlock_mutations", [])
    assert [m for m in muts if m.op == "inject"] == []


@pytest.mark.asyncio
async def test_litellm_params_path_no_double_record(
    interceptor: EnhancedModelInterceptor, config_fallback_env
) -> None:
    # A request whose enhanced_profile IS in litellm_params records exactly once
    # via Site 11 — never a second config-fallback record.
    data = {
        "model": "gemini-coding",
        "messages": [{"role": "user", "content": "hi"}],
        "litellm_params": {
            "enhanced_profile": {
                "target_model": "physical-model",
                "system_prompt": "inline sys",
                "name": "coder",
            }
        },
    }
    result = await interceptor.async_pre_call(data)
    muts = result["metadata"]["airlock_mutations"]
    inj = [m for m in muts if m.op == "inject" and m.field == "system"]
    assert len(inj) == 1
    assert inj[0].source == "enhanced.interceptor"
    assert [m for m in muts if m.source == "enhanced.passthrough"] == []
