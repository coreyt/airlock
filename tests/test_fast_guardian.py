"""Tests for airlock/fast/guardian.py"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from litellm.exceptions import RateLimitError

from airlock.fast.guardian import (
    AirlockFastGuardian,
    _extract_client_id,
    _request_client_id,
)
from airlock.guardrails.extract import extract_text_from_messages as _extract_text


# ---------------------------------------------------------------------------
# _extract_client_id()
# ---------------------------------------------------------------------------
class TestExtractClientId:
    def test_from_api_key_attribute(self):
        mock = MagicMock()
        mock.api_key = "sk-1234567890abcdef"
        assert _extract_client_id(mock) == "key:90abcdef"

    def test_from_dict(self):
        d = {"api_key": "sk-1234567890abcdef"}
        assert _extract_client_id(d) == "key:90abcdef"

    def test_short_key_fallback(self):
        mock = MagicMock()
        mock.api_key = "short"
        # len <= 8, so it won't match the first branch
        result = _extract_client_id(mock)
        # falls to dict check, mock is not a dict, so "unknown"
        assert isinstance(result, str)

    def test_none_returns_unknown(self):
        assert _extract_client_id(None) == "no_client"

    def test_empty_dict_returns_unknown(self):
        result = _extract_client_id({})
        assert result == "no_client"

    def test_request_client_id_prefers_airlock_header(self):
        data = {
            "headers": {"X-Airlock-Client": "harness-live:claude-sonnet"},
            "metadata": {},
        }
        d = {"api_key": "sk-1234567890abcdef"}
        assert _request_client_id(data, d) == "harness-live:claude-sonnet"


# ---------------------------------------------------------------------------
# _extract_text()
# ---------------------------------------------------------------------------
class TestExtractText:
    def test_string_content(self):
        messages = [{"role": "user", "content": "hello"}]
        assert "hello" in _extract_text(messages)

    def test_multipart_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
        result = _extract_text(messages)
        assert "Describe" in result
        assert "data:" not in result


# ---------------------------------------------------------------------------
# AirlockFastGuardian.async_pre_call_hook()
# ---------------------------------------------------------------------------
class TestGuardianPreCallHook:
    @pytest.fixture
    def guardian(self):
        return AirlockFastGuardian()

    async def test_normal_request_passes(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "metadata" in result
        assert "airlock_priority" in result["metadata"]
        assert "score" in result["metadata"]["airlock_priority"]

    async def test_reasoning_effort_none_normalized_in_hook(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        # Wiring check: the pre-call hook normalizes an off-intent reasoning_effort
        # before litellm's drop_params would silently strip it. anthropic -> dropped.
        data = {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "claude-sonnet",
            "reasoning_effort": "none",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert "reasoning_effort" not in result  # anthropic: off-intent -> no thinking

    async def test_client_in_backoff_rejected(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        client = fresh_state_store.get_client(client_id)
        client.backoff_until = time.time() + 60

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="Too many requests"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_high_threat_blocked(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        client = fresh_state_store.get_client(client_id)
        now = time.time()
        # Rapid-fire + high score to trigger threat block
        for i in range(20):
            client.record_request(now - 2 + i * 0.05)
            client.record_error(now - i * 0.05, "Error")
        client.threat_score = 0.8

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(ValueError, match="unusual activity"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_open_circuit_pinned_request_returns_429(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        # Break claude-sonnet
        model = fresh_state_store.get_model("claude-sonnet")
        now = time.time()
        for _ in range(5):
            model.record_failure(now)

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(RateLimitError, match="protect upstream standing"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_all_circuits_open_rejected(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        now = time.time()
        for model_name in ["claude-sonnet", "claude-haiku", "gpt-4o"]:
            model = fresh_state_store.get_model(model_name)
            for _ in range(5):
                model.record_failure(now)

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(RateLimitError, match="protect upstream standing"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_record_request_called(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        client = fresh_state_store.get_client(client_id)
        assert len(client.request_times) == 1

    async def test_pinned_request_disables_downstream_fallbacks_and_retries(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result["disable_fallbacks"] is True
        assert result["num_retries"] == 0
        assert result["max_retries"] == 0
        assert result["metadata"]["airlock_pinned_request"]["disable_fallbacks"] is True

    async def test_unknown_api_key(self, guardian, fresh_state_store, mock_cache):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            None, mock_cache, data, "completion"
        )
        assert "airlock_priority" in result.get("metadata", {})
        assert result["metadata"]["airlock_request"]["client_id"] == "no_client"

    async def test_request_metadata_uses_airlock_header_client(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
            "headers": {"X-Airlock-Client": "harness-live:claude-sonnet"},
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert (
            result["metadata"]["airlock_request"]["client_id"]
            == "harness-live:claude-sonnet"
        )

    async def test_gemini_text_only_mode_maps_reasoning_control(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "gemini-pro",
            "metadata": {"airlock": {"gemini": {"mode": "text_only"}}},
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result["reasoning_effort"] == "disable"
        assert result["metadata"]["airlock_gemini"]["mode"] == "text_only"

    async def test_pinned_quarantined_provider_returns_429(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        now = time.time()
        fresh_state_store.record_provider_rate_limit(
            client_id,
            "anthropic",
            now,
            "quota exhausted",
            "RateLimitError",
        )

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(RateLimitError, match="protect upstream standing"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )

    async def test_pinned_quarantined_provider_writes_precall_block_record(
        self,
        guardian,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        monkeypatch,
    ):
        client_id = _extract_client_id(mock_user_api_key_dict)
        now = time.time()
        fresh_state_store.record_provider_rate_limit(
            client_id,
            "anthropic",
            now,
            "quota exhausted",
            "RateLimitError",
        )

        written: list[dict] = []
        monkeypatch.setattr(
            "airlock.fast.guardian.write_precall_block_record",
            lambda data, **kwargs: written.append({"data": data, **kwargs}),
        )

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        with pytest.raises(RateLimitError):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "completion"
            )
        assert written
        assert written[0]["error_type"] == "RateLimitError"

    async def test_unpinned_request_fails_over_and_sets_override_metadata(
        self,
        guardian,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        monkeypatch,
    ):
        # Failover is config-driven now (SET-unify) — no hidden default map. Supply a
        # map that fails the quarantined anthropic model over to a healthy provider.
        import json

        monkeypatch.setenv(
            "AIRLOCK_FAILOVER_MAP",
            json.dumps({"claude-haiku": ["gpt-5-mini", "gemini-flash"]}),
        )
        client_id = _extract_client_id(mock_user_api_key_dict)
        now = time.time()
        fresh_state_store.record_provider_rate_limit(
            client_id,
            "anthropic",
            now,
            "quota exhausted",
            "RateLimitError",
        )

        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "smart",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert result["model"] != "claude-sonnet"
        assert (
            result["metadata"]["airlock_model_override"]["final_model"]
            == result["model"]
        )
        assert (
            result["metadata"]["airlock_response_headers"]["X-Airlock-Model-Override"]
            == result["model"]
        )


# ---------------------------------------------------------------------------
# Null-route handling (batch/file routes with no top-level model)
# ---------------------------------------------------------------------------
class TestGuardianNullRouteCoercion:
    """Batch/file routes (/v1/batches, /v1/files) have no top-level model.

    The guardian must coerce requested_model = data.get("model") or "unknown"
    and not crash when processing these routes.
    """

    @pytest.fixture
    def guardian(self):
        return AirlockFastGuardian()

    async def test_missing_model_field_coerced_to_unknown(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """Batch route with no 'model' field should coerce to 'unknown'."""
        data = {
            "messages": [{"role": "user", "content": "placeholder"}],
            # NO 'model' field — typical of /v1/batches payloads
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Should not crash and should record the request with "unknown" model
        assert result["metadata"]["airlock_request"]["requested_model"] == "unknown"
        assert result["metadata"]["airlock_priority"]["score"] is not None

    async def test_none_model_coerced_to_unknown(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """Batch route with model=None should coerce to 'unknown'."""
        data = {
            "messages": [{"role": "user", "content": "placeholder"}],
            "model": None,
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Should not crash and should record the request with "unknown" model
        assert result["metadata"]["airlock_request"]["requested_model"] == "unknown"
        assert result["metadata"]["airlock_priority"]["score"] is not None

    async def test_empty_string_model_coerced_to_unknown(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """Batch route with model="" should coerce to 'unknown'."""
        data = {
            "messages": [{"role": "user", "content": "placeholder"}],
            "model": "",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Should not crash and should record the request with "unknown" model
        assert result["metadata"]["airlock_request"]["requested_model"] == "unknown"
        assert result["metadata"]["airlock_priority"]["score"] is not None

    async def test_no_model_no_crash_full_flow(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """Full flow: guardian should not crash with model-less data."""
        # This simulates a /v1/files or /v1/batches request
        data = {
            "file": "batch-input.jsonl",
            # No 'model' field at all
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        # Verify all critical metadata is present and valid
        assert "airlock_priority" in result.get("metadata", {})
        assert "airlock_request" in result.get("metadata", {})
        assert result["metadata"]["airlock_request"]["requested_model"] == "unknown"
        # Should continue without crashing
        assert result is not None


# ---------------------------------------------------------------------------
# MCP call handling
# ---------------------------------------------------------------------------
class TestMCPCallHandling:
    @pytest.fixture
    def guardian(self):
        return AirlockFastGuardian()

    async def test_mcp_skips_routing_and_circuit_breaker(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """MCP calls should skip routing and circuit breaker but still run threat + priority."""
        data = {
            "mcp_tool_name": "read_file",
            "mcp_arguments": {"path": "/tmp/test.txt"},
            "model": "unknown",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
        )
        # Priority should be set
        assert "airlock_priority" in result.get("metadata", {})
        # No failover metadata (circuit breaker didn't run)
        assert "airlock_failover" not in result.get("metadata", {})

    async def test_mcp_threat_still_applies(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """MCP calls still get threat-checked (backoff applies)."""
        import time

        client_id = _extract_client_id(mock_user_api_key_dict)
        client = fresh_state_store.get_client(client_id)
        client.backoff_until = time.time() + 60  # force backoff

        data = {
            "mcp_tool_name": "search",
            "mcp_arguments": {"q": "test"},
        }
        with pytest.raises(ValueError, match="Too many requests"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "call_mcp_tool"
            )


# ---------------------------------------------------------------------------
# Batch / file call handling
# ---------------------------------------------------------------------------
class TestBatchCallHandling:
    @pytest.fixture
    def guardian(self):
        return AirlockFastGuardian()

    async def test_batch_skips_routing_and_circuit_breaker(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """Batch calls skip routing/circuit breaker but still get priority."""
        # Break claude-sonnet so a pinned completion would 429.
        now = time.time()
        model = fresh_state_store.get_model("claude-sonnet")
        for _ in range(5):
            model.record_failure(now)

        data = {
            "input_file_id": "file-abc",
            "model": "claude-sonnet",
        }
        # Circuit breaker is skipped → no RateLimitError despite broken model.
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "acreate_batch"
        )
        assert "airlock_priority" in result.get("metadata", {})
        assert "airlock_failover" not in result.get("metadata", {})

    async def test_batch_no_model_no_crash(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """Batch route with no/None model must not crash."""
        data = {"input_file_id": "file-abc"}
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "acreate_batch"
        )
        assert result["metadata"]["airlock_request"]["requested_model"] == "unknown"
        assert "airlock_priority" in result.get("metadata", {})

    async def test_batch_call_type_no_model_no_crash(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """File-create route (no model field) must not crash."""
        data = {"model": None, "purpose": "batch"}
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "acreate_file"
        )
        assert result["metadata"]["airlock_request"]["requested_model"] == "unknown"
        assert "airlock_priority" in result.get("metadata", {})

    async def test_batch_threat_still_applies(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """Batch calls still get threat-checked (backoff applies)."""
        client_id = _extract_client_id(mock_user_api_key_dict)
        client = fresh_state_store.get_client(client_id)
        client.backoff_until = time.time() + 60  # force backoff

        data = {"input_file_id": "file-abc"}
        with pytest.raises(ValueError, match="Too many requests"):
            await guardian.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, data, "acreate_batch"
            )

    async def test_normal_completion_still_routes(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        """A normal completion is unaffected by the batch gate."""
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        assert (
            result["metadata"]["airlock_request"]["requested_model"] == "claude-sonnet"
        )


# ---------------------------------------------------------------------------
# OBS-ledger — mutation records appended into airlock_mutations
# ---------------------------------------------------------------------------
class TestGuardianLedger:
    @pytest.fixture
    def guardian(self):
        return AirlockFastGuardian()

    async def test_pinned_request_records_fallback_suppression(
        self, guardian, fresh_state_store, mock_cache, mock_user_api_key_dict
    ):
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        muts = result["metadata"]["airlock_mutations"]
        suppress = [m for m in muts if m.field == "fallbacks" and m.op == "suppress"]
        assert len(suppress) == 1
        assert suppress[0].source == "guardian.pin"
        assert suppress[0].stage == "pre_call"
        # CC-T1 back-compat + CC-T4 behavior unchanged
        assert result["metadata"]["airlock_pinned_request"]["disable_fallbacks"] is True
        assert result["disable_fallbacks"] is True

    async def test_alias_resolution_records_model_rewrite(
        self,
        guardian,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        monkeypatch,
    ):
        import airlock.fast.guardian as gmod

        monkeypatch.setattr(
            gmod.alias_table,
            "resolve",
            lambda m: "claude-haiku" if m == "claude-sonnet" else None,
        )
        data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        muts = result["metadata"]["airlock_mutations"]
        alias = [m for m in muts if m.source == "guardian.alias"]
        assert len(alias) == 1
        assert alias[0].op == "rewrite"
        assert alias[0].field == "model"
        assert alias[0].before == "claude-sonnet"
        assert alias[0].after == "claude-haiku"
        # CC-T1
        assert result["metadata"]["airlock_alias"]["resolved"] == "claude-haiku"

    async def test_failover_records_model_rewrite(
        self,
        guardian,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        monkeypatch,
    ):
        import json

        import airlock.fast.guardian as gmod

        monkeypatch.setenv("AIRLOCK_FAILOVER_MAP", json.dumps({"model-a": ["model-b"]}))
        monkeypatch.setattr(gmod, "_is_client_pinned", lambda m, d: False)
        now = time.time()
        broken = fresh_state_store.get_model("model-a")
        for _ in range(5):
            broken.record_failure(now)
        data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "model": "model-a",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        muts = result["metadata"]["airlock_mutations"]
        fo = [m for m in muts if m.source == "guardian.failover"]
        assert len(fo) == 1
        assert fo[0].op == "rewrite"
        assert fo[0].before == "model-a"
        assert fo[0].after == "model-b"
        # CC-T1
        assert result["metadata"]["airlock_failover"]["failover_model"] == "model-b"

    async def test_drop_params_records_drop(
        self,
        guardian,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        monkeypatch,
    ):
        import airlock.fast.guardian as gmod

        monkeypatch.setattr(
            gmod, "detect_dropped_params", lambda data, model, provider: ["temperature"]
        )
        data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "model": "claude-sonnet",
            "temperature": 0.5,
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        muts = result["metadata"]["airlock_mutations"]
        drops = [m for m in muts if m.source == "drop_params"]
        assert len(drops) == 1
        assert drops[0].field == "temperature"
        assert drops[0].op == "drop"
        assert drops[0].stage == "pre_call"
        assert drops[0].reason == "provider-unsupported (drop_params)"

    async def test_drop_params_none_when_supported(
        self,
        guardian,
        fresh_state_store,
        mock_cache,
        mock_user_api_key_dict,
        monkeypatch,
    ):
        import airlock.fast.guardian as gmod

        monkeypatch.setattr(
            gmod, "detect_dropped_params", lambda data, model, provider: []
        )
        data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "model": "claude-sonnet",
        }
        result = await guardian.async_pre_call_hook(
            mock_user_api_key_dict, mock_cache, data, "completion"
        )
        muts = result["metadata"].get("airlock_mutations", [])
        assert [m for m in muts if m.source == "drop_params"] == []
        assert "airlock_priority" in result["metadata"]
