"""
Feature test harness fixtures.

Layered on top of root tests/conftest.py. Adds integration-level fixtures
for exercising features end-to-end in mock mode (direct calls) and live
mode (real proxy with API keys, gated by --run-live).
"""

from __future__ import annotations

import os

import pytest
import yaml


# ---------------------------------------------------------------------------
# CLI flag: --run-live
# ---------------------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run live integration tests against a real proxy instance",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="needs --run-live flag")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# ---------------------------------------------------------------------------
# Proxy connection fixtures (for live tests)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def proxy_url():
    return os.getenv("AIRLOCK_URL", "http://localhost:4000")


@pytest.fixture(scope="session")
def proxy_key():
    return os.getenv("AIRLOCK_MASTER_KEY", "sk-test-harness")


@pytest.fixture
async def http_client(proxy_url, proxy_key):
    import httpx

    async with httpx.AsyncClient(
        base_url=proxy_url,
        headers={"Authorization": f"Bearer {proxy_key}"},
        timeout=30.0,
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Harness-specific fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def harness_log_dir(tmp_path, monkeypatch):
    """Isolated log dir for harness tests — sets AIRLOCK_LOG_DIR."""
    log_path = tmp_path / "harness_logs"
    log_path.mkdir()
    monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_path))
    return log_path


@pytest.fixture
def harness_config(tmp_path):
    """Full config.yaml with all models + guardrails in tmp_path."""
    config = {
        "model_list": [
            {
                "model_name": "claude-haiku",
                "litellm_params": {
                    "model": "anthropic/claude-haiku-4-5-20251001",
                    "api_key": "os.environ/ANTHROPIC_API_KEY",
                },
            },
            {
                "model_name": "claude-sonnet",
                "litellm_params": {
                    "model": "anthropic/claude-sonnet-4-20250514",
                    "api_key": "os.environ/ANTHROPIC_API_KEY",
                },
            },
            {
                "model_name": "gpt-4o",
                "litellm_params": {
                    "model": "openai/gpt-4o",
                    "api_key": "os.environ/OPENAI_API_KEY",
                },
            },
            {
                "model_name": "gpt-4o-mini",
                "litellm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "os.environ/OPENAI_API_KEY",
                },
            },
            {
                "model_name": "gemini-flash",
                "litellm_params": {
                    "model": "gemini/gemini-2.5-flash",
                    "api_key": "os.environ/GOOGLE_AISTUDIO_API_KEY",
                },
            },
        ],
        "litellm_settings": {
            "drop_params": True,
            "num_retries": 0,
        },
        "general_settings": {
            "master_key": "os.environ/AIRLOCK_MASTER_KEY",
        },
        "router_settings": {
            "routing_strategy": "cost-based-routing",
        },
        "guardrails": [
            {
                "guardrail_name": "airlock-pii-guard",
                "litellm_params": {
                    "guardrail": "airlock.guardrails.pii_guard",
                    "mode": ["pre_call", "pre_mcp_call"],
                    "default_on": True,
                },
            },
            {
                "guardrail_name": "airlock-keyword-guard",
                "litellm_params": {
                    "guardrail": "airlock.guardrails.keyword_guard",
                    "mode": ["pre_call", "pre_mcp_call"],
                    "default_on": True,
                },
            },
        ],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@pytest.fixture
def completion_request():
    """Factory for chat completion request dicts."""

    def _make(
        model: str = "claude-sonnet",
        content: str = "What is the capital of France?",
        max_tokens: int = 50,
        **kwargs,
    ) -> dict:
        data = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        data.update(kwargs)
        return data

    return _make


@pytest.fixture
def guardrail_chain(monkeypatch):
    """Factory: instantiates guardrails in pipeline order, returns list."""

    def _make(keywords: str = "classified,topsecret"):
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", keywords)
        from airlock.guardrails.pii_guard import AirlockPIIGuard
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard
        from airlock.guardrails.enforcer import AirlockEnforcer

        return [
            AirlockPIIGuard(),
            AirlockKeywordGuard(),
            AirlockEnforcer(),
        ]

    return _make
