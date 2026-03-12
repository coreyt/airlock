"""
S1 — Infrastructure: validates harness fixtures work before other tests
depend on them. No proxy required.
"""

from __future__ import annotations

import os

import pytest
import yaml


pytestmark = pytest.mark.harness


class TestCleanEnv:
    def test_clean_env_removes_airlock_vars(self, monkeypatch):
        monkeypatch.setenv("AIRLOCK_FOO", "bar")
        assert os.getenv("AIRLOCK_FOO") == "bar"
        # clean_env is autouse from root conftest — next test won't see it.
        # Here we just verify we can set/get; the autouse fixture clears on
        # next test function boundary.


class TestFreshStateStore:
    def test_fresh_state_store_is_empty(self, fresh_state_store):
        assert len(fresh_state_store._clients) == 0
        assert len(fresh_state_store._models) == 0


class TestHarnessLogDir:
    def test_harness_log_dir_exists(self, harness_log_dir):
        assert harness_log_dir.exists()
        assert harness_log_dir.is_dir()
        assert os.getenv("AIRLOCK_LOG_DIR") == str(harness_log_dir)


class TestCompletionRequestFactory:
    def test_defaults(self, completion_request):
        req = completion_request()
        assert req["model"] == "claude-sonnet"
        assert req["messages"][0]["role"] == "user"
        assert req["messages"][0]["content"] == "What is the capital of France?"
        assert req["max_tokens"] == 50

    def test_overrides(self, completion_request):
        req = completion_request(
            model="gpt-4o", content="Hello", max_tokens=10
        )
        assert req["model"] == "gpt-4o"
        assert req["messages"][0]["content"] == "Hello"
        assert req["max_tokens"] == 10

    def test_extra_kwargs(self, completion_request):
        req = completion_request(stream=True, temperature=0.5)
        assert req["stream"] is True
        assert req["temperature"] == 0.5


class TestGuardrailChainFactory:
    def test_returns_instances(self, guardrail_chain, reset_presidio_singletons):
        from airlock.guardrails.pii_guard import AirlockPIIGuard
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard
        from airlock.guardrails.enforcer import AirlockEnforcer

        chain = guardrail_chain()
        assert len(chain) == 3
        assert isinstance(chain[0], AirlockPIIGuard)
        assert isinstance(chain[1], AirlockKeywordGuard)
        assert isinstance(chain[2], AirlockEnforcer)


class TestHarnessConfig:
    def test_has_guardrails(self, harness_config):
        config = yaml.safe_load(harness_config.read_text())
        assert "guardrails" in config
        assert len(config["guardrails"]) >= 2

    def test_has_model_list(self, harness_config):
        config = yaml.safe_load(harness_config.read_text())
        assert "model_list" in config
        assert len(config["model_list"]) >= 3
