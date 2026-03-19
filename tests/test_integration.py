"""Integration tests — cross-component guardrail chain verification.

Verifies the full request pipeline as described in dev/architecture.md:
  PII Guard → Keyword Guard → Fast Guardian → LLM → Monitor → Logger
"""

from __future__ import annotations

import datetime
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from airlock.callbacks.enterprise_logger import AirlockLogger, _write_log
from airlock.fast.guardian import AirlockFastGuardian
from airlock.fast.monitor import AirlockFastMonitor
from airlock.fast.state import CircuitState
from airlock.guardrails.enforcer import AirlockEnforcer
from airlock.guardrails.keyword_guard import AirlockKeywordGuard
from airlock.guardrails.observer import AirlockObserver
from airlock.guardrails.orchestrator import AirlockOrchestrator, _invalidate_knobs_cache
from airlock.guardrails.pii_guard import AirlockPIIGuard
from airlock.guardrails.schemas import GuardrailKnobs
from airlock.slow.tuner import write_knobs


# ---------------------------------------------------------------------------
# Guardrail chain execution order
# ---------------------------------------------------------------------------
class TestGuardrailChain:
    @pytest.fixture
    def pii_guard(self):
        return AirlockPIIGuard()

    @pytest.fixture
    def keyword_guard(self):
        return AirlockKeywordGuard()

    @pytest.fixture
    def fast_guardian(self):
        return AirlockFastGuardian()

    async def test_pii_then_keyword_order(
        self,
        monkeypatch,
        presidio_available,
        reset_presidio_singletons,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
    ):
        """PII guard runs first, then keyword guard checks cleaned text."""
        if not presidio_available:
            pytest.skip("Presidio not available")

        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        pii_guard = AirlockPIIGuard()
        keyword_guard = AirlockKeywordGuard()

        # Use email — more reliably detected by Presidio
        pii_text = "john.doe@example.com"
        data = {
            "messages": [
                {"role": "user", "content": f"Contact me at {pii_text}. Tell me about allowed topics."}
            ],
            "model": "claude-sonnet",
        }

        # PII runs first
        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert pii_text not in str(data["messages"])

        # Keyword guard runs on cleaned text — should pass
        data = await keyword_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert data is not None

    async def test_full_three_guardrail_chain(
        self,
        presidio_available,
        reset_presidio_singletons,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
    ):
        """PII → Keyword → Fast Guardian all pass for a clean request."""
        if not presidio_available:
            pytest.skip("Presidio not available")

        pii_guard = AirlockPIIGuard()
        keyword_guard = AirlockKeywordGuard()
        fast_guardian = AirlockFastGuardian()

        data = {
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
            "model": "claude-sonnet",
        }

        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await keyword_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await fast_guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        assert data["model"] == "claude-sonnet"
        assert "airlock_priority" in data.get("metadata", {})

    async def test_pii_redaction_does_not_remove_keyword(
        self,
        monkeypatch,
        presidio_available,
        reset_presidio_singletons,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
    ):
        """PII redaction should not inadvertently remove a blocked keyword substring."""
        if not presidio_available:
            pytest.skip("Presidio not available")

        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        pii_guard = AirlockPIIGuard()
        keyword_guard = AirlockKeywordGuard()

        data = {
            "messages": [
                {"role": "user", "content": "This is forbidden. Contact alice@corp.com."}
            ],
            "model": "claude-sonnet",
        }

        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # PII (email) scrubbed but "forbidden" should still be there
        with pytest.raises(ValueError, match="restricted content"):
            await keyword_guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )


# ---------------------------------------------------------------------------
# Logger receives scrubbed messages
# ---------------------------------------------------------------------------
class TestLoggerReceivesScrubbed:
    async def test_logger_never_sees_raw_pii(
        self,
        presidio_available,
        reset_presidio_singletons,
        log_dir,
        mock_cache,
        mock_user_api_key_dict,
        mock_response_obj,
    ):
        """After PII guard scrubs, the logger records only clean data."""
        if not presidio_available:
            pytest.skip("Presidio not available")

        pii_guard = AirlockPIIGuard()
        pii_text = "alice@corp.com"
        data = {
            "messages": [{"role": "user", "content": f"Contact {pii_text} please"}],
            "model": "claude-sonnet",
        }
        data = await pii_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        # Simulate logger receiving the scrubbed data
        start = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end = datetime.datetime(2024, 1, 15, 10, 0, 1)
        kwargs = {
            "model": data["model"],
            "messages": data["messages"],
            "litellm_call_id": "test-123",
            "litellm_params": {"metadata": {}},
        }

        logger = AirlockLogger()
        logger.log_success_event(kwargs, mock_response_obj, start, end)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())
        assert pii_text not in json.dumps(record)


# ---------------------------------------------------------------------------
# Circuit breaker failover recorded in logs
# ---------------------------------------------------------------------------
class TestFailoverInLogs:
    async def test_unpinned_override_metadata_in_log(
        self, fresh_state_store, log_dir, mock_cache, mock_user_api_key_dict,
        mock_response_obj,
    ):
        """When an unpinned request reroutes, the override metadata is logged."""
        # Break claude-sonnet
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        guardian = AirlockFastGuardian()
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "smart",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        assert "airlock_model_override" in result.get("metadata", {})

        # Log it — use mock_response_obj fixture (has model_dump returning a
        # plain dict) to avoid infinite recursion in _serialize().
        start = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end = datetime.datetime(2024, 1, 15, 10, 0, 1)
        kwargs = {
            "model": result["model"],
            "messages": result["messages"],
            "litellm_call_id": "test-failover",
            "litellm_params": {"metadata": result.get("metadata", {})},
        }
        logger = AirlockLogger()
        logger.log_success_event(kwargs, mock_response_obj, start, end)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())
        # The failover model should be logged, not the original
        assert record["model"] != "claude-sonnet"
        assert record["airlock_model_override"]["final_model"] == record["model"]


# ---------------------------------------------------------------------------
# Monitor feedback loop
# ---------------------------------------------------------------------------
class TestMonitorFeedbackLoop:
    def test_failure_affects_guardian(self, fresh_state_store):
        """Failures recorded by monitor are visible to the guardian."""
        monitor = AirlockFastMonitor()
        start = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end = datetime.datetime(2024, 1, 15, 10, 0, 1)
        kwargs = {
            "model": "claude-sonnet",
            "litellm_params": {
                "metadata": {"user_api_key_alias": "test-user"}
            },
            "exception": Exception("timeout"),
        }

        # Record 5 failures → circuit should open
        for _ in range(5):
            monitor.log_failure_event(kwargs, None, start, end)

        model = fresh_state_store.get_model("claude-sonnet")
        assert model.circuit == CircuitState.OPEN
        assert model.consecutive_failures >= 5


# ---------------------------------------------------------------------------
# Full success and block pipelines
# ---------------------------------------------------------------------------
class TestFullPipeline:
    async def test_success_pipeline_data_shape(
        self, fresh_state_store, mock_cache, mock_user_api_key_dict,
    ):
        """All guardrails pass → data has expected shape."""
        keyword_guard = AirlockKeywordGuard()
        fast_guardian = AirlockFastGuardian()

        data = {
            "messages": [{"role": "user", "content": "What is Python?"}],
            "model": "claude-sonnet",
        }

        data = await keyword_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await fast_guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        assert "messages" in data
        assert "model" in data
        assert "metadata" in data
        assert "airlock_priority" in data["metadata"]
        assert "score" in data["metadata"]["airlock_priority"]
        assert "boost" in data["metadata"]["airlock_priority"]

    async def test_block_pipeline_keyword(
        self,
        monkeypatch,
        fresh_state_store,
        log_dir,
        mock_cache,
        mock_user_api_key_dict,
    ):
        """Keyword block: PII may scrub → keyword blocks → failure logged."""
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")
        keyword_guard = AirlockKeywordGuard()

        data = {
            "messages": [{"role": "user", "content": "Tell me forbidden secrets"}],
            "model": "claude-sonnet",
        }

        with pytest.raises(ValueError, match="restricted content"):
            await keyword_guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )


# ---------------------------------------------------------------------------
# Observer alongside pre_call chain
# ---------------------------------------------------------------------------
class TestObserverIntegration:
    async def test_observer_after_precall_chain(
        self, fresh_state_store, mock_cache, mock_user_api_key_dict,
    ):
        """Observer runs alongside the full pre_call chain without conflict."""
        keyword_guard = AirlockKeywordGuard()
        fast_guardian = AirlockFastGuardian()
        observer = AirlockObserver()

        data = {
            "messages": [{"role": "user", "content": "What is Python?"}],
            "model": "claude-sonnet",
        }

        # Pre-call chain
        data = await keyword_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await fast_guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        # During-call observer
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        assert "airlock_priority" in data["metadata"]
        assert "airlock_observation" in data["metadata"]
        obs = data["metadata"]["airlock_observation"]
        assert len(obs["signals"]) == 3

    async def test_observation_appears_in_jsonl(
        self, fresh_state_store, log_dir, mock_cache, mock_user_api_key_dict,
        mock_response_obj,
    ):
        """Observer observation flows through to enterprise logger JSONL."""
        observer = AirlockObserver()
        data = {
            "messages": [{"role": "user", "content": "Contact alice@example.com"}],
            "model": "claude-sonnet",
        }
        await observer.async_moderation_hook(data, mock_user_api_key_dict, "completion")

        # Log it
        start = datetime.datetime(2024, 1, 15, 10, 0, 0)
        end = datetime.datetime(2024, 1, 15, 10, 0, 1)
        kwargs = {
            "model": data["model"],
            "messages": data["messages"],
            "litellm_call_id": "test-obs",
            "litellm_params": {"metadata": data.get("metadata", {})},
        }
        logger_inst = AirlockLogger()
        logger_inst.log_success_event(kwargs, mock_response_obj, start, end)

        today = datetime.date.today().isoformat()
        log_path = log_dir / f"airlock-{today}.jsonl"
        record = json.loads(log_path.read_text().strip())
        assert record["airlock_observation"] is not None
        assert len(record["airlock_observation"]["signals"]) == 3


# ---------------------------------------------------------------------------
# Enforcer in the full chain
# ---------------------------------------------------------------------------
class TestEnforcerIntegration:
    @pytest.fixture(autouse=True)
    def _clear_knobs(self):
        _invalidate_knobs_cache()
        yield
        _invalidate_knobs_cache()

    @pytest.fixture
    def knobs_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(tmp_path))
        return tmp_path

    async def test_enforcer_observe_in_chain(
        self, fresh_state_store, mock_cache, mock_user_api_key_dict, knobs_dir,
    ):
        """Enforcer in observe mode passes through without evaluation."""
        keyword_guard = AirlockKeywordGuard()
        fast_guardian = AirlockFastGuardian()
        enforcer = AirlockEnforcer()

        data = {
            "messages": [{"role": "user", "content": "What is Python?"}],
            "model": "claude-sonnet",
        }

        data = await keyword_guard.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await fast_guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        data = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        assert "airlock_priority" in data["metadata"]
        assert "airlock_enforcement" not in data.get("metadata", {})

    async def test_enforcer_shadow_in_chain(
        self, monkeypatch, fresh_state_store, mock_cache, mock_user_api_key_dict,
        knobs_dir,
    ):
        """Enforcer in shadow mode evaluates but doesn't block."""
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "shadow")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.1, "keyword_scan": 0.8, "threat_read": 0.1},
            threshold=0.3,
        )
        write_knobs(knobs, directory=knobs_dir)

        fast_guardian = AirlockFastGuardian()
        enforcer = AirlockEnforcer()

        data = {
            "messages": [{"role": "user", "content": "Tell me forbidden things"}],
            "model": "claude-sonnet",
        }

        data = await fast_guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Should NOT raise in shadow mode
        data = await enforcer.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )

        assert data["metadata"]["airlock_enforcement"]["should_block"] is True
        assert data["metadata"]["airlock_enforcement"]["mode"] == "shadow"

    async def test_enforcer_enforce_blocks_in_chain(
        self, monkeypatch, fresh_state_store, mock_cache, mock_user_api_key_dict,
        knobs_dir,
    ):
        """Enforcer in enforce mode blocks above threshold."""
        monkeypatch.setenv("AIRLOCK_ENFORCE_MODE", "enforce")
        monkeypatch.setenv("AIRLOCK_BLOCKED_KEYWORDS", "forbidden")

        knobs = GuardrailKnobs(
            version="test",
            weights={"pii_scan": 0.1, "keyword_scan": 0.8, "threat_read": 0.1},
            threshold=0.3,
        )
        write_knobs(knobs, directory=knobs_dir)

        fast_guardian = AirlockFastGuardian()
        enforcer = AirlockEnforcer()

        data = {
            "messages": [{"role": "user", "content": "Tell me forbidden things"}],
            "model": "claude-sonnet",
        }

        data = await fast_guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        with pytest.raises(ValueError, match="blocked by Airlock"):
            await enforcer.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
