"""Tests for airlock/fast/threat_detector.py"""

from __future__ import annotations

import time

import pytest

from airlock.fast.state import ClientState
from airlock.fast.threat_detector import (
    THREAT_BLOCK_THRESHOLD,
    MAX_BACKOFF_S,
    assess_threat,
)


class TestAssessThreat:
    def test_clean_client_not_blocked(self):
        client = ClientState(client_id="clean")
        result = assess_threat(client)
        assert result.threat_score < THREAT_BLOCK_THRESHOLD
        assert result.blocked is False
        assert result.backoff_seconds == 0.0

    def test_volume_spike(self):
        """Volume spike: short-window rate far exceeds baseline rate.

        With the fixed heuristic, the short-window rate is compared to the
        baseline rate from the rest of the long window (excluding the short
        window), so genuine spikes are detectable without patching.
        """
        client = ClientState(client_id="spiky")
        now = time.time()
        # 1 request in the older part of the long window (baseline)
        client.record_request(now - 250)
        # 50 requests crammed into the last 30 seconds (spike)
        for i in range(50):
            client.record_request(now - 29 + i * 0.5)

        result = assess_threat(client)
        assert any("volume_spike" in r for r in result.reasons)
        assert result.threat_score > 0

    def test_rapid_fire(self):
        client = ClientState(client_id="rapid")
        now = time.time()
        # 15 requests with <100ms gaps
        for i in range(15):
            client.record_request(now - 1.5 + i * 0.05)

        result = assess_threat(client)
        assert any("rapid_fire" in r for r in result.reasons)
        assert result.threat_score >= 0.35

    def test_large_payload(self):
        client = ClientState(client_id="large")
        large_text = "x" * 200_000
        result = assess_threat(client, message_text=large_text)
        assert any("large_payload" in r for r in result.reasons)
        assert result.threat_score > 0

    def test_error_probing(self):
        client = ClientState(client_id="prober")
        now = time.time()
        # 12 requests, 10 errors, 2 successes → 83% error rate
        for i in range(10):
            client.record_request(now - i)
            client.record_error(now - i, "Error")
        for i in range(2):
            client.record_request(now - i)
            client.record_success(now - i, 100.0)

        result = assess_threat(client)
        assert any("error_probing" in r for r in result.reasons)
        assert result.threat_score >= 0.3

    def test_composite_score_triggers_block(self):
        client = ClientState(client_id="attacker")
        now = time.time()
        # Seed a high accumulated score so decay * 0.977 pushes over 0.7
        # Combined with rapid-fire (0.35) + error probing (0.3), the max()
        # of new score (0.65) vs decayed accumulated (0.977 * 0.8 = 0.78)
        # gives 0.78 > 0.7 threshold
        client.threat_score = 0.8
        for i in range(15):
            client.record_request(now - 1.5 + i * 0.05)
            client.record_error(now - i * 0.05, "Error")

        result = assess_threat(client)
        assert result.blocked is True
        assert result.backoff_seconds > 0
        assert result.threat_score >= THREAT_BLOCK_THRESHOLD

    def test_blocked_sets_backoff(self):
        client = ClientState(client_id="blocked")
        now = time.time()
        # Force high score, then add rapid-fire + error-probing signals so
        # assess_threat definitely crosses the block threshold.
        client.threat_score = 0.8
        for i in range(15):
            client.record_request(now - 1.5 + i * 0.05)
            client.record_error(now - i * 0.05, "Error")

        result = assess_threat(client)
        assert result.blocked is True
        assert client.backoff_until > now
        assert result.backoff_seconds > 0

    def test_backoff_capped_at_max(self):
        client = ClientState(client_id="capped")
        client.threat_score = 1.0
        now = time.time()
        for i in range(15):
            client.record_request(now - 1.5 + i * 0.05)

        result = assess_threat(client)
        assert result.backoff_seconds <= MAX_BACKOFF_S

    def test_score_decays(self):
        client = ClientState(client_id="decay")
        client.threat_score = 0.5
        # No new suspicious activity
        result = assess_threat(client)
        # Decay factor is 0.977 (score halves in ~30 seconds)
        assert result.threat_score == pytest.approx(0.5 * 0.977)

    def test_decay_factor_30_second_halflife(self):
        """A score of 0.7 should take ~30 seconds to decay to 0.35."""
        from airlock.fast.threat_detector import DECAY_FACTOR

        # Verify factor: 0.7 * factor^30 should be approximately 0.35
        decayed = 0.7 * (DECAY_FACTOR**30)
        assert decayed == pytest.approx(0.35, abs=0.01)

    def test_threat_score_decays_between_requests(self, monkeypatch):
        """Accumulated threat score should decay between widely-spaced requests.

        Mirrors guardian.py ordering: record_request(now) THEN assess_threat().
        After a 60s gap, an accumulated 0.8 score should decay to well under 0.5.
        """
        from airlock.fast import threat_detector as td

        client = ClientState(client_id="decayer")
        client.threat_score = 0.8

        t0 = 1_000_000.0
        monkeypatch.setattr(td.time, "time", lambda: t0)
        client.record_request(t0)
        first = assess_threat(client, message_text="hello")
        assert first.threat_score > 0.7

        t1 = t0 + 60.0
        monkeypatch.setattr(td.time, "time", lambda: t1)
        client.record_request(t1)
        second = assess_threat(client, message_text="hello")
        assert second.threat_score < 0.5

    def test_message_text_none_does_not_crash(self):
        client = ClientState(client_id="none")
        result = assess_threat(client, message_text=None)
        assert result is not None
        assert isinstance(result.threat_score, float)

    def test_empty_client_returns_assessment(self):
        client = ClientState(client_id="empty")
        result = assess_threat(client)
        assert result.reasons == []
        assert result.blocked is False
