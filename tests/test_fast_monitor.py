"""Tests for airlock/fast/monitor.py"""

from __future__ import annotations

import datetime

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
        assert _extract_client_id(kwargs) == "airlock:codex-review"

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
        self, monitor, fresh_state_store, mock_logger_kwargs,
        mock_response_obj, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.successes) == 1
        assert len(client.latencies_ms) == 1

        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.success_times) == 1

    def test_failure_updates_client_and_model(
        self, monitor, fresh_state_store, mock_failure_kwargs, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_failure_event(
            mock_failure_kwargs, None, start, end
        )

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.errors) == 1

        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.failure_times) == 1
        assert model.consecutive_failures == 1

    def test_duration_calculated_correctly(
        self, monitor, fresh_state_store, mock_logger_kwargs,
        mock_response_obj, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )

        client = fresh_state_store.get_client("user:dev-alice")
        _, latency = client.latencies_ms[0]
        assert abs(latency - 1500.0) < 1.0  # 1.5s = 1500ms

    async def test_async_success_delegates(
        self, monitor, fresh_state_store, mock_logger_kwargs,
        mock_response_obj, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        await monitor.async_log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.successes) == 1

    async def test_async_failure_delegates(
        self, monitor, fresh_state_store, mock_failure_kwargs, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        await monitor.async_log_failure_event(
            mock_failure_kwargs, None, start, end
        )

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.errors) == 1

    def test_multiple_events_accumulate(
        self, monitor, fresh_state_store, mock_logger_kwargs,
        mock_response_obj, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        for _ in range(5):
            monitor.log_success_event(
                mock_logger_kwargs, mock_response_obj, start, end
            )

        client = fresh_state_store.get_client("user:dev-alice")
        assert len(client.successes) == 5

        model = fresh_state_store.get_model("claude-sonnet")
        assert len(model.success_times) == 5
        assert model.consecutive_failures == 0

    def test_mcp_success_tracks_tool_state(
        self, monitor, fresh_state_store, mock_start_end_times,
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
        self, monitor, fresh_state_store, mock_start_end_times,
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
        self, monitor, fresh_state_store, mock_logger_kwargs,
        mock_response_obj, mock_start_end_times,
    ):
        start, end = mock_start_end_times
        monitor.log_success_event(
            mock_logger_kwargs, mock_response_obj, start, end
        )
        llm, mcp = fresh_state_store.traffic_split()
        assert llm == 1
        assert mcp == 0

    def test_rate_limit_failure_quarantines_client_provider(
        self, monitor, fresh_state_store, mock_logger_kwargs, mock_start_end_times,
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

        client_provider = fresh_state_store.get_client_provider("user:dev-alice", "openai")
        assert client_provider.is_quarantined(end.timestamp())
        metadata = kwargs["litellm_params"]["metadata"]
        assert metadata["airlock_provider_protection"]["action"] == "client_quarantine"

    def test_multiple_clients_escalate_provider_quarantine(
        self, monitor, fresh_state_store, mock_start_end_times,
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
        assert kwargs2["litellm_params"]["metadata"]["airlock_provider_protection"]["action"] == "provider_quarantine"
