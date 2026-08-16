"""Contract tests for unused configured-provider startup warnings."""

from __future__ import annotations

from io import StringIO

import pytest

from airlock.startup_validation import (
    credential_without_alias_warnings,
    emit_provider_credential_warnings,
)


def _write_config(path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def test_present_recognised_credential_without_alias_warns_redacted(tmp_path):
    config = tmp_path / "config.yaml"
    _write_config(config, "model_list: []\n")

    warnings = credential_without_alias_warnings(
        config, {"OPENROUTER_API_KEY": "sk-sentinel-secret"}.get
    )

    assert [warning.provider for warning in warnings] == ["openrouter"]
    output = StringIO()
    emit_provider_credential_warnings(warnings, stream=output)
    text = output.getvalue()
    assert "airlock.startup.provider_credential_without_alias" in text
    assert "provider=openrouter" in text
    assert "credential_configured=true" in text
    assert "configured_alias_count=0" in text
    assert "sk-sentinel-secret" not in text
    assert "OPENROUTER_API_KEY" not in text


def test_enabled_provider_and_blank_or_unknown_credentials_do_not_warn(tmp_path):
    config = tmp_path / "config.yaml"
    _write_config(
        config,
        "model_list:\n"
        "  - model_name: reviewed\n"
        "    litellm_params: {model: openai/gpt-4o-mini}\n",
    )

    warnings = credential_without_alias_warnings(
        config,
        {
            "OPENAI_API_KEY": "present",
            "ANTHROPIC_API_KEY": "   ",
            "UNRECOGNISED_API_KEY": "present",
        }.get,
    )
    assert warnings == ()


@pytest.mark.parametrize(
    ("provider", "environment_variable", "model"),
    [
        ("anthropic", "ANTHROPIC_API_KEY", "anthropic/claude"),
        ("openai", "OPENAI_API_KEY", "openai/gpt"),
        ("gemini", "GOOGLE_AISTUDIO_API_KEY", "gemini/flash"),
        ("mistral", "MISTRAL_API_KEY", "mistral/large"),
        ("openrouter", "OPENROUTER_API_KEY", "openrouter/openai/gpt"),
        ("deepseek", "DEEPSEEK_API_KEY", "deepseek/chat"),
        ("perplexity", "PERPLEXITY_API_KEY", "perplexity/sonar"),
        ("tavily", "TAVILY_API_KEY", "tavily/search"),
    ],
)
def test_each_recognised_provider_is_suppressed_by_a_matching_alias(
    tmp_path, provider, environment_variable, model
):
    config = tmp_path / "config.yaml"
    _write_config(
        config,
        "model_list:\n"
        f"  - model_name: {provider}\n"
        f"    litellm_params: {{model: {model}}}\n",
    )
    assert (
        credential_without_alias_warnings(config, {environment_variable: "yes"}.get)
        == ()
    )


def test_direct_include_extends_model_list_but_nested_include_is_ignored(tmp_path):
    child = tmp_path / "child.yaml"
    nested = tmp_path / "nested.yaml"
    config = tmp_path / "config.yaml"
    _write_config(nested, "model_list:\n  - litellm_params: {model: openrouter/a}\n")
    _write_config(
        child,
        "include: [nested.yaml]\n"
        "model_list:\n  - litellm_params: {model: openai/gpt-4o-mini}\n",
    )
    _write_config(config, "include: [child.yaml]\nmodel_list: []\n")

    warnings = credential_without_alias_warnings(
        config, {"OPENAI_API_KEY": "yes", "OPENROUTER_API_KEY": "yes"}.get
    )
    assert [warning.provider for warning in warnings] == ["openrouter"]


def test_explicit_vllm_backend_counts_as_enabled(tmp_path):
    config = tmp_path / "config.yaml"
    _write_config(
        config,
        "model_list:\n"
        "  - model_name: local\n"
        "    litellm_params:\n"
        "      model: openai/local\n"
        "      api_key: os.environ/VLLM_API_KEY\n"
        "      backend: vllm\n",
    )
    assert credential_without_alias_warnings(config, {"VLLM_API_KEY": "yes"}.get) == ()


def test_invalid_include_fails_without_disclosing_config_contents(tmp_path):
    config = tmp_path / "config.yaml"
    _write_config(config, "include: not-a-list\nmodel_list: []\n")
    with pytest.raises(ValueError, match="list of paths"):
        credential_without_alias_warnings(config, {"OPENAI_API_KEY": "yes"}.get)
