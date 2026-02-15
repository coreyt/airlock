"""Tests for airlock/fast/priority.py"""

from __future__ import annotations

import time

import pytest

from airlock.fast.priority import (
    BOOST_THRESHOLD,
    compute_priority,
)
from airlock.fast.state import ClientState


class TestComputePriority:
    def test_idle_client_zero_score(self):
        client = ClientState(client_id="idle")
        result = compute_priority(client)
        assert result.score == 0.0
        assert result.boost is False
        assert result.reasons == []

    def test_interactive_cadence_signal(self):
        client = ClientState(client_id="active")
        now = time.time()
        # 5 requests with 10s gaps → interactive session
        for i in range(5):
            client.record_request(now - 50 + i * 10)

        result = compute_priority(client)
        assert any("interactive_session" in r for r in result.reasons)
        assert result.score > 0

    def test_recovery_need_signal(self):
        client = ClientState(client_id="failing")
        now = time.time()
        # 40% error rate (4 errors, 6 successes)
        for i in range(6):
            client.record_success(now - i, 100.0)
        for i in range(4):
            client.record_error(now - i, "Error")

        result = compute_priority(client)
        assert any("recovery_need" in r for r in result.reasons)
        assert result.score > 0

    def test_latency_spike_signal(self):
        client = ClientState(client_id="slow")
        now = time.time()
        # Baseline: many 200ms samples outside the 5-min window
        # to dominate the 30-min average (WINDOW_SECONDS * 6 = 1800s)
        for i in range(50):
            client.record_success(now - 1700 + i * 20, 200.0)
        # Recent: 3 samples at 1000ms within 5-min window
        # recent_avg=1000, baseline_avg dominated by 200ms → ratio ~5x
        for i in range(3):
            client.record_success(now - i * 10, 1000.0)

        result = compute_priority(client)
        assert any("latency_spike" in r for r in result.reasons)

    def test_starvation_signal(self):
        client = ClientState(client_id="starving")
        now = time.time()
        # 8 requests, 60% error rate
        for i in range(8):
            client.record_request(now - i)
        for i in range(5):
            client.record_error(now - i, "Error")
        for i in range(3):
            client.record_success(now - i, 100.0)

        result = compute_priority(client)
        assert any("starvation" in r for r in result.reasons)

    def test_combined_score_triggers_boost(self):
        client = ClientState(client_id="needy")
        now = time.time()
        # Interactive cadence: tight gaps (5s), enough requests
        for i in range(8):
            client.record_request(now - 40 + i * 5)
        # High error rate: 70% errors → recovery_need + starvation
        for i in range(7):
            client.record_error(now - i, "Error")
        for i in range(3):
            client.record_success(now - i, 100.0)

        result = compute_priority(client)
        assert result.score >= BOOST_THRESHOLD
        assert result.boost is True

    def test_score_capped_at_1(self):
        client = ClientState(client_id="extreme")
        now = time.time()
        # All signals firing
        for i in range(10):
            client.record_request(now - i * 5)
        for i in range(10):
            client.record_error(now - i, "Error")
        # Baseline latency low, recent high
        for i in range(5):
            client.record_success(now - 1800 + i * 60, 100.0)
        for i in range(3):
            client.record_success(now - i, 1000.0)

        result = compute_priority(client)
        assert result.score <= 1.0

    def test_no_boost_below_threshold(self):
        client = ClientState(client_id="lowpri")
        now = time.time()
        # Just a few requests, no errors → score should be low
        for i in range(3):
            client.record_request(now - i * 30)
            client.record_success(now - i * 30, 200.0)

        result = compute_priority(client)
        assert result.score < BOOST_THRESHOLD
        assert result.boost is False
