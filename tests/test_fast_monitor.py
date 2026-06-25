"""Tests for airlock/fast/monitor.py"""

from __future__ import annotations


import pytest
from litellm.exceptions import RateLimitError

from airlock.fast.monitor import AirlockFastMonitor, _extract_client_id


# ---------------------------------------------------------------------------
# _extract_client_id()
# ---------------------------------------------------------------------------
class TestExtractClientId:
    def test_api_key_preferred_over_alias(self):
        """API key is preferred when present (matches guardian's client ID logic)."""
        kwargs = {
            "litellm_params": {
                "metadata": {
                    "user_api_key_alias": "dev-alice",
                    "user_api_key_user_id": "alice",
                    "user_api_key": "sk-1234567890abcdef",
                }
            }
        }
        assert _extract_client_id(kwargs) == "key:90abcdef"

    def test_airlock_client_header_preferred(self):
        kwargs = {
            "headers": {"X-Airlock-Client": "codex-review"},
            "litellm_params": {
                "metadata": {
                    "user_api_key_alias": "dev-alice",
                    "user_api_key": "sk-1234567890abcdef",
                }
            },
        }
        assert _extract_client_id(kwargs) == "codex-review"

    def test_alias_fallback_no_key(self):
        """Falls back to alias when API key is short/missing."""
        kwargs = {
            "litellm_params": {
                "metadata": {
                    "user_api_key_alias": "dev-alice",
                    "user_api_key_user_id": "alice",
                    "user_api_key": "sk-short",
                }
            }
        }
        assert _extract_client_id(kwargs) == "user:dev-alice"

    def test_user_id_fallback(self):
        kwargs = {
            "litellm_params": {
                "metadata": {
                    "user_api_key_user_id": "alice",
                }
            }
        }
        assert _extract_client_id(kwargs) == "user:alice"

    def test_api_key_suffix_fallback(self):
        kwargs = {
            "litellm_params": {
                "metadata": {
                    "user_api_key": "sk-1234567890abcdef",
                }
            }
        }
        assert _extract_client_id(kwargs) == "key:90abcdef"

    def test_unknown_fallback(self):
        kwargs = {"litellm_params": {"metadata": {}}}
        assert _extract_client_id(kwargs) == "no_client"

    def test_missing_metadata(self):
        kwargs = {"litellm_params": {}}
        assert _extract_client_id(kwargs) == "no_client"

    def test_missing_litellm_params(self):
        assert _extract_client_id({}) == "no_client"


# ---------------------------------------------------------------------------
# AirlockFastMonitor callbacks
# ---------------------------------------------------------------------------
class TestMonitorCallbacks:
    @pytest.fixture
    def monitor(self):
        return AirlockFastMonitor()

    def test_success_updates_client_and_model(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.successes) == 1
        assert len(client.latencies_ms) == 1

        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.success_times) == 1

    def test_failure_updates_client_and_model(
        self,
        monitor,
        fresh_state_store,
        mock_failure_kwargs,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_failure_event(mock_failure_kwargs, None, start, end)

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.errors) == 1

        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.failure_times) == 1
        assert model.consecutive_failures == 1

    def test_precall_failure_skips_circuit_breaker(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
    ):
        """Auth/pre-call failures (exception=None) must not trip the circuit breaker."""
        start, end = mock_start_end_times
        kwargs = {**mock_logger_kwargs, "exception": None}
        monitor.log_failure_event(kwargs, None, start, end)

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.errors) == 1  # client error still recorded

        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.failure_times) == 0  # circuit breaker NOT affected
        assert model.consecutive_failures == 0

    def test_precall_block_does_not_feed_provider_breaker(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
    ):
        """A1-b no-re-arm invariant: a pre-call block (exception=None) must NOT
        call record_provider_rate_limit, so an Airlock quarantine can never feed
        itself a fresh 429 and re-arm the cooldown."""
        from unittest.mock import patch

        start, end = mock_start_end_times
        kwargs = {**mock_logger_kwargs, "exception": None}
        with patch.object(
            fresh_state_store, "record_provider_rate_limit"
        ) as mock_rprl:
            monitor.log_failure_event(kwargs, None, start, end)
        mock_rprl.assert_not_called()

    def test_duration_calculated_correctly(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        client = fresh_state_store.get_client("user:dev-alice")
        _, latency = client.latencies_ms[0]
        assert abs(latency - 1500.0) < 1.0  # 1.5s = 1500ms

    async def test_async_success_delegates(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        await monitor.async_log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.successes) == 1

    async def test_async_failure_delegates(
        self,
        monitor,
        fresh_state_store,
        mock_failure_kwargs,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        await monitor.async_log_failure_event(mock_failure_kwargs, None, start, end)

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.errors) == 1

    def test_multiple_events_accumulate(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        for _ in range(5):
            monitor.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.successes) == 5

        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.success_times) == 5
        assert model.consecutive_failures == 0

    def test_mcp_success_tracks_tool_state(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "mcp-proxy",
            "call_type": "call_mcp_tool",
            "mcp_tool_name": "read_file",
            "mcp_server_name": "filesystem",
            "litellm_params": {"metadata": {}},
        }
        monitor.log_success_event(kwargs, None, start, end)

        tool = fresh_state_store.get_mcp_tool("read_file", "filesystem")
        assert len(tool.success_times) == 1
        assert len(tool.latencies_ms) == 1

        llm, mcp = fresh_state_store.traffic_split()
        assert mcp == 1
        assert llm == 0

    def test_mcp_failure_tracks_tool_state(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "mcp-proxy",
            "call_type": "call_mcp_tool",
            "mcp_tool_name": "write_file",
            "mcp_server_name": "filesystem",
            "exception": Exception("tool error"),
            "litellm_params": {"metadata": {}},
        }
        monitor.log_failure_event(kwargs, None, start, end)

        tool = fresh_state_store.get_mcp_tool("write_file", "filesystem")
        assert len(tool.failure_times) == 1

        llm, mcp = fresh_state_store.traffic_split()
        assert mcp == 1

    def test_llm_call_tracks_as_llm(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)
        llm, mcp = fresh_state_store.traffic_split()
        assert llm == 1
        assert mcp == 0

    def test_batch_success_skips_model_stats(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
    ):
        """Batch events must not pollute model latency/health stats."""
        start, end = mock_start_end_times
        kwargs = {
            "model": "gpt-4o-batch",
            "call_type": "create_batch",
            "litellm_params": {"metadata": {"user_api_key_alias": "dev-alice"}},
        }
        monitor.log_success_event(kwargs, None, start, end)

        # Client-level accounting still happens (consistent with mcp handling).
        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.successes) == 1

        # Model-level latency/health stats are NOT touched by batch.
        model = fresh_state_store.get_model("gpt-4o-batch")
        assert len(model.success_times) == 0

    def test_batch_failure_skips_model_health(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
    ):
        """Batch failures must not feed model circuit-breaker health."""
        start, end = mock_start_end_times
        kwargs = {
            "model": "gpt-4o-batch",
            "call_type": "create_batch",
            "exception": Exception("boom"),
            "litellm_params": {"metadata": {"user_api_key_alias": "dev-alice"}},
        }
        monitor.log_failure_event(kwargs, None, start, end)

        model = fresh_state_store.get_model("gpt-4o-batch")
        assert len(model.failure_times) == 0
        assert model.consecutive_failures == 0

    def test_normal_success_still_updates_model_stats(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_response_obj,
        mock_start_end_times,
    ):
        """A non-batch event still updates model latency/health stats."""
        start, end = mock_start_end_times
        monitor.log_success_event(mock_logger_kwargs, mock_response_obj, start, end)
        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.success_times) == 1

    def test_rate_limit_failure_quarantines_client_provider(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            **mock_logger_kwargs,
            "model": "gpt-4o",
            "exception": RateLimitError(
                message="You exceeded your current quota",
                llm_provider="openai",
                model="gpt-4o",
            ),
        }

        monitor.log_failure_event(kwargs, None, start, end)

        client_provider = fresh_state_store.get_client_provider(
            "user:dev-alice", "openai"
        )
        assert client_provider.is_quarantined(end.timestamp())
        metadata = kwargs["litellm_params"]["metadata"]
        assert metadata["airlock_provider_protection"]["action"] == "client_quarantine"

    def test_multiple_clients_escalate_provider_quarantine(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        base_kwargs = {
            "model": "gpt-4o",
            "litellm_params": {"metadata": {}},
            "exception": RateLimitError(
                message="quota",
                llm_provider="openai",
                model="gpt-4o",
            ),
        }
        kwargs1 = {
            **base_kwargs,
            "headers": {"X-Airlock-Client": "client-a"},
            "litellm_params": {"metadata": {}},
        }
        kwargs2 = {
            **base_kwargs,
            "headers": {"X-Airlock-Client": "client-b"},
            "litellm_params": {"metadata": {}},
        }

        monitor.log_failure_event(kwargs1, None, start, end)
        monitor.log_failure_event(kwargs2, None, start, end)

        provider = fresh_state_store.get_provider("openai")
        assert provider.is_quarantined(end.timestamp())
        assert (
            kwargs2["litellm_params"]["metadata"]["airlock_provider_protection"][
                "action"
            ]
            == "provider_quarantine"
        )

    def test_gemini_success_tracks_output_shape(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "gemini-pro",
            "headers": {"X-Airlock-Client": "gemini-client"},
            "litellm_params": {
                "metadata": {"airlock_gemini": {"mode": "deep_reasoning"}}
            },
        }
        response = type(
            "Resp",
            (),
            {
                "model_dump": lambda self: {
                    "choices": [
                        {"message": {"content": None}, "finish_reason": "length"}
                    ],
                    "usage": {
                        "completion_tokens_details": {
                            "reasoning_tokens": 4,
                            "text_tokens": 0,
                        }
                    },
                }
            },
        )()

        monitor.log_success_event(kwargs, response, start, end)

        client = fresh_state_store.get_client("gemini-client")
        provider = fresh_state_store.get_provider("gemini")
        assert client.recent_gemini_outcome_count("thought_only") == 1
        assert provider.recent_gemini_outcome_count("thought_only") == 1

    def test_rate_limit_quarantine_uses_same_airlock_client_bucket_as_guardian(
        self,
        monitor,
        fresh_state_store,
        mock_start_end_times,
    ):
        start, end = mock_start_end_times
        kwargs = {
            "model": "gpt-4o-mini",
            "headers": {"X-Airlock-Client": "same-client"},
            "litellm_params": {"metadata": {}},
            "exception": RateLimitError(
                message="quota",
                llm_provider="openai",
                model="gpt-4o-mini",
            ),
        }

        monitor.log_failure_event(kwargs, None, start, end)

        client_provider = fresh_state_store.get_client_provider("same-client", "openai")
        assert client_provider.is_quarantined(end.timestamp())


# ---------------------------------------------------------------------------
# OBS-accounting (CC-T4): spend keys off the SERVED provider on success;
# quarantine keys off the ERROR's provider on failure. Flag-gated (default on).
# ---------------------------------------------------------------------------
def _served_response(hidden_params: dict | None):
    """A minimal response object carrying ``_hidden_params`` (or none)."""

    class _Resp:
        pass

    resp = _Resp()
    if hidden_params is not None:
        resp._hidden_params = hidden_params
    return resp


class TestServedAccounting:
    @pytest.fixture
    def monitor(self):
        return AirlockFastMonitor()

    def test_success_spend_keys_off_served_provider(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
    ):
        """Served≠inferred: spend is debited to the served provider, not inferred."""
        start, end = mock_start_end_times
        # model "claude-sonnet" infers anthropic, but it was served by bedrock.
        response = _served_response(
            {"custom_llm_provider": "bedrock", "response_cost": 0.02}
        )
        kwargs = {**mock_logger_kwargs, "response_cost": 0.02}

        monitor.log_success_event(kwargs, response, start, end)

        assert fresh_state_store.get_provider_spend("bedrock").recent_spend() == 0.02
        assert fresh_state_store.get_provider_spend("anthropic").recent_spend() == 0.0

    def test_success_served_cost_none_falls_back_to_kwargs_cost(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
    ):
        """Served provider but no served cost → amount from kwargs, keyed to served."""
        start, end = mock_start_end_times
        response = _served_response({"custom_llm_provider": "bedrock"})
        kwargs = {**mock_logger_kwargs, "response_cost": 0.05}

        monitor.log_success_event(kwargs, response, start, end)

        assert fresh_state_store.get_provider_spend("bedrock").recent_spend() == 0.05
        assert fresh_state_store.get_provider_spend("anthropic").recent_spend() == 0.0

    def test_success_no_served_read_uses_inferred(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
    ):
        """No _hidden_params → old behavior: inferred provider + kwargs cost."""
        start, end = mock_start_end_times
        response = _served_response(None)
        kwargs = {**mock_logger_kwargs, "response_cost": 0.03}

        monitor.log_success_event(kwargs, response, start, end)

        assert fresh_state_store.get_provider_spend("anthropic").recent_spend() == 0.03
        assert fresh_state_store.get_provider_spend("bedrock").recent_spend() == 0.0

    def test_rate_limit_no_response_keys_off_error_provider(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
    ):
        """429 with response_obj=None → quarantine keyed off exception.llm_provider."""
        start, end = mock_start_end_times
        # model "claude-sonnet" infers anthropic; the 429 came from openai.
        kwargs = {
            **mock_logger_kwargs,
            "exception": RateLimitError(
                message="You exceeded your current quota",
                llm_provider="openai",
                model="claude-sonnet",
            ),
        }

        monitor.log_failure_event(kwargs, None, start, end)

        client_provider = fresh_state_store.get_client_provider(
            "user:dev-alice", "openai"
        )
        assert client_provider.is_quarantined(end.timestamp())
        metadata = kwargs["litellm_params"]["metadata"]
        assert metadata["airlock_provider"] == "openai"
        assert metadata["airlock_provider_protection"]["provider"] == "openai"

    def test_flag_off_success_uses_inferred(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
        monkeypatch,
    ):
        """Flag OFF restores inferred spend keying on success."""
        from airlock.transparency import TransparencyConfig

        monkeypatch.setattr(
            "airlock.fast.monitor.get_transparency_config",
            lambda: TransparencyConfig(attribute_accounting_to_served=False),
        )
        start, end = mock_start_end_times
        response = _served_response(
            {"custom_llm_provider": "bedrock", "response_cost": 0.02}
        )
        kwargs = {**mock_logger_kwargs, "response_cost": 0.02}

        monitor.log_success_event(kwargs, response, start, end)

        assert fresh_state_store.get_provider_spend("anthropic").recent_spend() == 0.02
        assert fresh_state_store.get_provider_spend("bedrock").recent_spend() == 0.0

    def test_flag_off_failure_uses_inferred(
        self,
        monitor,
        fresh_state_store,
        mock_logger_kwargs,
        mock_start_end_times,
        monkeypatch,
    ):
        """Flag OFF restores inferred quarantine keying on failure."""
        from airlock.transparency import TransparencyConfig

        monkeypatch.setattr(
            "airlock.fast.monitor.get_transparency_config",
            lambda: TransparencyConfig(attribute_accounting_to_served=False),
        )
        start, end = mock_start_end_times
        # model infers anthropic; exception says openai — flag off keeps anthropic.
        kwargs = {
            **mock_logger_kwargs,
            "exception": RateLimitError(
                message="quota",
                llm_provider="openai",
                model="claude-sonnet",
            ),
        }

        monitor.log_failure_event(kwargs, None, start, end)

        client_provider = fresh_state_store.get_client_provider(
            "user:dev-alice", "anthropic"
        )
        assert client_provider.is_quarantined(end.timestamp())
        metadata = kwargs["litellm_params"]["metadata"]
        assert metadata["airlock_provider"] == "anthropic"
