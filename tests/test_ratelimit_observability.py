"""Tests for workstream C / Pack 0.5.0-RES-observ (quota headroom observability)."""

from __future__ import annotations

import time

from airlock.fast.ratelimit_headers import parse_ratelimit_headers
from airlock.fast.state import ProviderRateLimitState, StateStore


class TestParseRatelimitHeaders:
    def test_openai_style(self):
        out = parse_ratelimit_headers(
            {
                "x-ratelimit-remaining-tokens": "12000",
                "x-ratelimit-remaining-requests": "59",
                "x-ratelimit-limit-tokens": "30000",
                "x-ratelimit-reset-tokens": "6m0s",
                "x-ratelimit-reset-requests": "1s",
            }
        )
        assert out["remaining_tokens"] == 12000
        assert out["remaining_requests"] == 59
        assert out["limit_tokens"] == 30000
        assert out["reset_tokens_seconds"] == 360.0
        assert out["reset_requests_seconds"] == 1.0

    def test_missing_keys_are_none(self):
        out = parse_ratelimit_headers({"x-ratelimit-remaining-tokens": "5"})
        assert out["remaining_tokens"] == 5
        assert out["remaining_requests"] is None
        assert out["reset_tokens_seconds"] is None

    def test_plain_seconds_and_unparseable(self):
        assert parse_ratelimit_headers({"x-ratelimit-reset-tokens": "12.5"})[
            "reset_tokens_seconds"
        ] == 12.5
        assert parse_ratelimit_headers({"x-ratelimit-reset-tokens": "soon"})[
            "reset_tokens_seconds"
        ] is None

    def test_non_mapping_input(self):
        out = parse_ratelimit_headers(None)
        assert all(v is None for v in out.values())

    def test_compound_duration(self):
        out = parse_ratelimit_headers({"x-ratelimit-reset-requests": "1h2m3s"})
        assert out["reset_requests_seconds"] == 3723.0


class TestProviderRateLimitState:
    def test_update_overlays_non_none(self):
        s = ProviderRateLimitState(provider="openai")
        s.update({"remaining_tokens": 100, "remaining_requests": None}, 123.0)
        assert s.remaining_tokens == 100
        assert s.observed_at == 123.0
        # a later partial update keeps the prior remaining_tokens
        s.update({"remaining_requests": 5}, 124.0)
        assert s.remaining_tokens == 100
        assert s.remaining_requests == 5

    def test_store_record_and_get(self):
        store = StateStore()
        store.record_provider_ratelimit(
            "openai", {"remaining_tokens": 7, "remaining_requests": 2}, time.time()
        )
        rl = store.get_provider_ratelimit("openai")
        assert rl.remaining_tokens == 7
        assert rl.remaining_requests == 2

    def test_store_noop_on_empty(self):
        store = StateStore()
        store.record_provider_ratelimit("openai", {"remaining_tokens": None}, time.time())
        # nothing observed -> default state, observed_at stays 0
        assert store.get_provider_ratelimit("openai").observed_at == 0.0


class TestMetricsHelperNoCrash:
    def test_headroom_helper_tolerates_none(self):
        from airlock.callbacks.metrics import record_provider_ratelimit_headroom

        record_provider_ratelimit_headroom("openai", None, None)  # must not raise
        record_provider_ratelimit_headroom("openai", 10, 3)


class TestRecordType:
    def test_request_record_has_record_type(self):
        from airlock.callbacks.enterprise_logger import AirlockLogger

        rec = AirlockLogger._build_record(
            {"model": "gpt-5.4", "messages": []},
            None,
            None,
            None,
            success=True,
        )
        assert rec["record_type"] == "request"


class TestObservFix1:
    """Coverage the RES-observ review flagged (remaining=0, case-insensitive,
    empty dict, and the monitor success/failure wiring)."""

    def test_zero_remaining_is_observed(self):
        # The most important 429 signal: fully exhausted quota.
        out = parse_ratelimit_headers({"x-ratelimit-remaining-tokens": "0"})
        assert out["remaining_tokens"] == 0
        store = StateStore()
        store.record_provider_ratelimit("openai", out, 9999.0)
        rl = store.get_provider_ratelimit("openai")
        assert rl.remaining_tokens == 0
        assert rl.observed_at == 9999.0

    def test_case_insensitive_keys(self):
        out = parse_ratelimit_headers({"X-RateLimit-Remaining-Tokens": "500"})
        assert out["remaining_tokens"] == 500

    def test_empty_dict(self):
        out = parse_ratelimit_headers({})
        assert all(v is None for v in out.values())

    def test_monitor_success_captures_headroom(self, fresh_state_store):
        from types import SimpleNamespace

        from airlock.fast.monitor import AirlockFastMonitor

        monitor = AirlockFastMonitor()
        kwargs = {
            "model": "gpt-5.4",
            "litellm_params": {"metadata": {"user_api_key": "sk-aaaaaaaa12345678"}},
            "response_cost": 0.0,
        }
        response_obj = SimpleNamespace(
            _hidden_params={
                "additional_headers": {"x-ratelimit-remaining-tokens": "777"}
            },
            usage=None,
        )
        monitor.log_success_event(kwargs, response_obj, None, None)
        assert fresh_state_store.get_provider_ratelimit("openai").remaining_tokens == 777

    def test_monitor_failure_captures_headroom(self, fresh_state_store):
        from types import SimpleNamespace

        from airlock.fast.monitor import AirlockFastMonitor

        monitor = AirlockFastMonitor()
        exc = RuntimeError("rate limit exceeded")
        exc.response = SimpleNamespace(
            headers={"x-ratelimit-remaining-requests": "3"}
        )
        kwargs = {
            "model": "gpt-5.4",
            "litellm_params": {"metadata": {"user_api_key": "sk-bbbbbbbb12345678"}},
            "exception": exc,
        }
        monitor.log_failure_event(kwargs, None, None, None)
        assert (
            fresh_state_store.get_provider_ratelimit("openai").remaining_requests == 3
        )
