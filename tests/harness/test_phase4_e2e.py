"""
S16 — E2E Scenarios: composite guardrail chain + logging.

Exercises multiple subsystems together in mock mode.
"""

from __future__ import annotations

import datetime

import pytest


pytestmark = pytest.mark.harness


class TestPIIAndLogging:
    async def test_pii_redacted_and_logged(
        self,
        guardrail_chain,
        mock_cache,
        mock_user_api_key_dict,
        harness_log_dir,
        reset_presidio_singletons,
        presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        from airlock.callbacks.enterprise_logger import _write_log

        chain = guardrail_chain(keywords="topsecret")
        data = {
            "messages": [{"role": "user", "content": "Card: 4111111111111111"}],
            "model": "claude-sonnet",
        }
        # Run through PII guard
        result = await chain[0].async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "4111111111111111" not in str(result["messages"])

        # Log the redacted data
        record = {"messages": result["messages"], "model": "claude-sonnet"}
        _write_log(record)
        today = datetime.date.today().isoformat()
        log_file = harness_log_dir / f"airlock-{today}.jsonl"
        log_content = log_file.read_text()
        assert "4111111111111111" not in log_content

    async def test_pii_log_no_raw_data(
        self,
        guardrail_chain,
        mock_cache,
        mock_user_api_key_dict,
        harness_log_dir,
        reset_presidio_singletons,
        presidio_available,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        from airlock.callbacks.enterprise_logger import _write_log

        chain = guardrail_chain(keywords="topsecret")
        data = {
            "messages": [
                {
                    "role": "user",
                    "content": "Email: alice@secret.com Card: 4111111111111111",
                }
            ],
            "model": "claude-sonnet",
        }
        result = await chain[0].async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        _write_log({"messages": result["messages"]})
        today = datetime.date.today().isoformat()
        log_file = harness_log_dir / f"airlock-{today}.jsonl"
        content = log_file.read_text()
        assert "alice@secret.com" not in content
        assert "4111111111111111" not in content


class TestThreatBurst:
    async def test_threat_burst_backoff(
        self,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
    ):
        import time as _time
        from airlock.fast.guardian import AirlockFastGuardian

        guardian = AirlockFastGuardian()
        # Simulate previous threat block by setting backoff
        client_id = f"key:{mock_user_api_key_dict.api_key[-8:]}"
        client = fresh_state_store.get_client(client_id)
        client.backoff_until = _time.time() + 60

        data = {
            "messages": [{"role": "user", "content": "ping"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="[Tt]oo many|[Rr]etry"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )


class TestSmartSessionPII:
    async def test_smart_session_pii(
        self,
        fresh_state_store,
        guardrail_chain,
        mock_cache,
        mock_user_api_key_dict,
        reset_presidio_singletons,
        presidio_available,
        monkeypatch,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        chain = guardrail_chain(keywords="topsecret")

        data = {
            "model": "smart",
            "messages": [
                {"role": "user", "content": "Contact alice@company.com about monads."}
            ],
            "metadata": {"airlock": {"session_id": "e2e-test-1"}},
        }
        # Smart routing
        routed = apply_routing(data)
        assert routed["model"] != "smart"

        # PII redaction
        result = await chain[0].async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, routed, "completion"
        )
        assert "alice@company.com" not in str(result["messages"])

    async def test_session_affinity_across_chain(
        self,
        fresh_state_store,
        monkeypatch,
    ):
        from airlock.fast.router import apply_routing

        monkeypatch.delenv("AIRLOCK_COST_TIERS", raising=False)
        models = []
        for i in range(3):
            data = {
                "model": "smart",
                "messages": [{"role": "user", "content": f"Request {i}"}],
                "metadata": {"airlock": {"session_id": "affinity-test"}},
            }
            result = apply_routing(data)
            models.append(result["model"])
        assert models[0] == models[1] == models[2]


class TestKeywordBlockLogged:
    async def test_keyword_block_logged(
        self,
        guardrail_chain,
        mock_cache,
        mock_user_api_key_dict,
        harness_log_dir,
    ):
        from airlock.callbacks.enterprise_logger import _write_log

        chain = guardrail_chain(keywords="classified")
        data = {
            "messages": [{"role": "user", "content": "classified info here"}],
            "model": "claude-sonnet",
        }
        blocked = False
        try:
            await chain[1].async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        except ValueError:
            blocked = True
            _write_log({"blocked": True, "reason": "keyword"})
        assert blocked
        today = datetime.date.today().isoformat()
        log_file = harness_log_dir / f"airlock-{today}.jsonl"
        content = log_file.read_text()
        assert "classified" not in content.replace('"keyword"', "")


class TestFullPipeline:
    async def test_full_pipeline_metadata(
        self,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        reset_presidio_singletons,
        presidio_available,
        monkeypatch,
    ):
        if not presidio_available:
            pytest.skip("Presidio not installed")
        from airlock.guardrails.pii_guard import AirlockPIIGuard
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard
        from airlock.guardrails.enforcer import AirlockEnforcer
        from airlock.guardrails.orchestrator import (
            AirlockOrchestrator,
            _invalidate_knobs_cache,
        )
        from airlock.guardrails.semantic import AirlockSemanticGuard, clear_classifiers

        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "topsecret")
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")
        _invalidate_knobs_cache()
        clear_classifiers()

        data = {
            "messages": [
                {
                    "role": "user",
                    "content": "My email is alice@company.com. What is Python?",
                }
            ],
            "model": "claude-sonnet",
        }

        # Pre-call chain
        pii = AirlockPIIGuard()
        data = await pii.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        kw = AirlockKeywordGuard()
        data = await kw.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        enforcer = AirlockEnforcer()
        data = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        # During-call
        semantic = AirlockSemanticGuard()
        await semantic.async_moderation_hook(data, mock_user_api_key_dict, "completion")
        orch = AirlockOrchestrator()
        await orch.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        metadata = data.get("metadata", {})
        assert "airlock_semantic" in metadata
        assert "airlock_observation" in metadata
        assert "airlock_enforcement" in metadata
        _invalidate_knobs_cache()

    async def test_full_pipeline_order(
        self,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        reset_presidio_singletons,
        presidio_available,
        monkeypatch,
    ):
        """Guards execute in documented order: PII → keyword → enforcer → semantic → orchestrator."""
        if not presidio_available:
            pytest.skip("Presidio not installed")
        from airlock.guardrails.pii_guard import AirlockPIIGuard
        from airlock.guardrails.keyword_guard import AirlockKeywordGuard
        from airlock.guardrails.enforcer import AirlockEnforcer
        from airlock.guardrails.semantic import AirlockSemanticGuard, clear_classifiers

        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "topsecret")
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "observe")
        clear_classifiers()

        execution_order = []

        class TrackedPII(AirlockPIIGuard):
            async def async_pre_call_hook(self, *args, **kwargs):
                execution_order.append("pii")
                return await super().async_pre_call_hook(*args, **kwargs)

        class TrackedKeyword(AirlockKeywordGuard):
            async def async_pre_call_hook(self, *args, **kwargs):
                execution_order.append("keyword")
                return await super().async_pre_call_hook(*args, **kwargs)

        class TrackedEnforcer(AirlockEnforcer):
            async def async_pre_call_hook(self, *args, **kwargs):
                execution_order.append("enforcer")
                return await super().async_pre_call_hook(*args, **kwargs)

        class TrackedSemantic(AirlockSemanticGuard):
            async def async_moderation_hook(self, *args, **kwargs):
                execution_order.append("semantic")
                return await super().async_moderation_hook(*args, **kwargs)

        data = {
            "messages": [{"role": "user", "content": "What is Python?"}],
            "model": "claude-sonnet",
        }

        data = await TrackedPII().async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await TrackedKeyword().async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await TrackedEnforcer().async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        await TrackedSemantic().async_moderation_hook(
            data, mock_user_api_key_dict, "completion"
        )

        assert execution_order == ["pii", "keyword", "enforcer", "semantic"]


class TestLiveE2E:
    @pytest.mark.live
    async def test_live_pii_plus_logging(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-haiku",
                "messages": [
                    {"role": "user", "content": "Card: 4111111111111111. Say OK."}
                ],
                "max_tokens": 10,
            },
        )
        assert resp.status_code == 200

    @pytest.mark.live
    async def test_live_full_pipeline(self, http_client):
        resp = await http_client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-haiku",
                "messages": [
                    {
                        "role": "user",
                        "content": "My email is alice@company.com. Say hello.",
                    }
                ],
                "max_tokens": 10,
            },
        )
        assert resp.status_code == 200
