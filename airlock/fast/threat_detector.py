"""
Airlock Fast — Threat / exploit detection with exponential back-off.

Four heuristics run on every inbound request:

  1. Volume spike — request rate in the last 30 s dramatically exceeds
     the client's 5-minute baseline.
  2. Rapid-fire — sub-100 ms inter-request gaps sustained across 10+
     requests (faster than any human could type).
  3. Payload anomaly — prompt text exceeding 100 k characters.
  4. Error probing — >80 % error rate over 10+ recent requests,
     suggesting API exploration or fuzzing.

When the composite threat_score crosses THREAT_BLOCK_THRESHOLD the
client is placed in exponential back-off: 2 s, 4 s, 8 s, …, up to
MAX_BACKOFF_S (1 hour).  The score decays slowly between evaluations
so that legitimate clients recover automatically.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .state import ClientState, WINDOW_SECONDS, store

logger = logging.getLogger("airlock.fast.threat")


@dataclass
class ThreatAssessment:
    """Result of threat evaluation for a single request."""

    threat_score: float  # 0.0 (safe) → 1.0 (definite threat)
    blocked: bool  # whether to reject the request
    backoff_seconds: float  # how long client must wait (0 if not blocked)
    reasons: list[str]  # what triggered the score


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
VOLUME_SPIKE_MULTIPLIER = 5.0  # 5× baseline rate triggers spike
RAPID_FIRE_MIN_GAP_S = 0.1  # <100 ms between requests
RAPID_FIRE_COUNT = 10  # need 10+ rapid-fire requests
LARGE_PAYLOAD_CHARS = 100_000  # >100 k chars is suspicious
ERROR_PROBE_RATE = 0.8  # >80 % errors = probing
ERROR_PROBE_MIN_REQUESTS = 10  # need enough samples
THREAT_BLOCK_THRESHOLD = 0.7  # above this → block
MAX_BACKOFF_S = 3600.0  # cap at 1 hour
BASE_BACKOFF_S = 2.0  # starting back-off
DECAY_FACTOR = 0.977  # per-second decay; score halves in ~30 s


def assess_threat(
    client: ClientState,
    message_text: str | None = None,
) -> ThreatAssessment:
    """Evaluate whether the current request looks like an attack."""
    now = time.time()
    score = 0.0
    reasons: list[str] = []

    # ----- Heuristic 1: Volume spike -----
    short_window = 30.0
    long_window = WINDOW_SECONDS
    short_count = sum(1 for t in client.request_times if t > now - short_window)
    long_count = sum(1 for t in client.request_times if t > now - long_window)

    # Compare short-window rate to the baseline rate from the *rest* of
    # the long window (excluding the short window).  This avoids the
    # previous bug where the max possible ratio was capped at exactly
    # long_window / short_window, making the threshold unreachable.
    baseline_count = long_count - short_count
    baseline_window = long_window - short_window
    if baseline_count > 0:
        short_rate = short_count / short_window
        baseline_rate = baseline_count / baseline_window
        if short_rate / baseline_rate > VOLUME_SPIKE_MULTIPLIER:
            spike = short_rate / baseline_rate
            score += min(0.4, (spike / VOLUME_SPIKE_MULTIPLIER - 1.0) * 0.1)
            reasons.append(f"volume_spike({spike:.1f}x)")

    # ----- Heuristic 2: Rapid-fire -----
    recent_times = sorted(t for t in client.request_times if t > now - short_window)
    if len(recent_times) >= RAPID_FIRE_COUNT:
        gaps = [
            recent_times[i] - recent_times[i - 1] for i in range(1, len(recent_times))
        ]
        rapid_count = sum(1 for g in gaps if g < RAPID_FIRE_MIN_GAP_S)
        if rapid_count >= RAPID_FIRE_COUNT - 1:
            score += 0.35
            reasons.append(f"rapid_fire(sub_100ms_gaps={rapid_count})")

    # ----- Heuristic 3: Payload anomaly -----
    if message_text and len(message_text) > LARGE_PAYLOAD_CHARS:
        payload_score = min(
            0.2,
            (len(message_text) - LARGE_PAYLOAD_CHARS) / 500_000 * 0.2,
        )
        score += payload_score
        reasons.append(f"large_payload(chars={len(message_text)})")

    # ----- Heuristic 4: Error probing -----
    error_rate = client.recent_error_rate(window_seconds=60.0)
    recent_requests = client.recent_request_count(window_seconds=60.0)
    if recent_requests >= ERROR_PROBE_MIN_REQUESTS and error_rate >= ERROR_PROBE_RATE:
        score += 0.3
        reasons.append(f"error_probing(rate={error_rate:.0%},n={recent_requests})")

    # Blend with the accumulated score, decayed proportionally to elapsed time
    # (0.977 per second: score halves in ~30 seconds). The read-modify-write of
    # threat_score / backoff_until is delegated to the store mutator so it runs
    # atomically under StateStore._lock — the heuristics above and the logging
    # below stay outside the lock.
    combined, blocked, backoff_seconds = store.record_threat_score(
        client,
        score,
        now,
        decay_base=DECAY_FACTOR,
        threshold=THREAT_BLOCK_THRESHOLD,
        base_backoff_s=BASE_BACKOFF_S,
        max_backoff_s=MAX_BACKOFF_S,
    )

    if blocked:
        logger.warning(
            "threat_blocked client=%s score=%.2f backoff=%.0fs reasons=%s",
            client.client_id,
            combined,
            backoff_seconds,
            reasons,
        )

    return ThreatAssessment(
        threat_score=combined,
        blocked=blocked,
        backoff_seconds=backoff_seconds,
        reasons=reasons,
    )
