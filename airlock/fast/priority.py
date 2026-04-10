"""
Airlock Fast — Priority scoring ("needs it" detection).

Determines which clients deserve a speed burst by evaluating four
real-time signals:

  1. Interactive cadence — regular, short-interval requests indicate an
     active coding session where latency directly impacts developer flow.
  2. Recovery need — a client whose recent requests have been failing
     needs the next request to succeed reliably.
  3. Latency spike — current response times far exceed the client's own
     baseline, suggesting the provider or route is degraded *for them*.
  4. Starvation — the client keeps trying but is consistently getting
     errors, indicating they are stuck and need relief.

A client "needs" priority (boost=True) when their composite score
crosses BOOST_THRESHOLD.  The score is surfaced as request metadata so
downstream routing or queue-priority logic can act on it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .state import ClientState, WINDOW_SECONDS


@dataclass
class PrioritySignal:
    """Result of priority evaluation for a single request."""

    score: float  # 0.0 (lowest) → 1.0 (highest)
    boost: bool  # whether this client gets a speed burst
    reasons: list[str]  # human-readable explanation


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
INTERACTIVE_GAP_MAX_S = 60.0  # requests ≤60 s apart = interactive
INTERACTIVE_MIN_REQUESTS = 3  # need ≥3 in window to judge cadence
ERROR_RATE_BOOST_THRESHOLD = 0.3  # >30 % errors → recovery signal
LATENCY_SPIKE_FACTOR = 2.0  # 2× above baseline → spike
BOOST_THRESHOLD = 0.6  # composite score ≥ this → boost


def compute_priority(client: ClientState) -> PrioritySignal:
    """Evaluate whether *client* currently needs a speed burst."""
    now = time.time()
    score = 0.0
    reasons: list[str] = []

    # ----- Signal 1: Interactive cadence -----
    recent_times = [t for t in client.request_times if t > now - WINDOW_SECONDS]
    if len(recent_times) >= INTERACTIVE_MIN_REQUESTS:
        gaps = [
            recent_times[i] - recent_times[i - 1] for i in range(1, len(recent_times))
        ]
        avg_gap = sum(gaps) / len(gaps) if gaps else float("inf")
        if avg_gap <= INTERACTIVE_GAP_MAX_S:
            cadence_score = max(0.0, 1.0 - (avg_gap / INTERACTIVE_GAP_MAX_S))
            score += cadence_score * 0.3
            reasons.append(f"interactive_session(avg_gap={avg_gap:.1f}s)")

    # ----- Signal 2: Recovery need -----
    error_rate = client.recent_error_rate()
    if error_rate >= ERROR_RATE_BOOST_THRESHOLD:
        recovery_score = min(1.0, error_rate / 0.8)
        score += recovery_score * 0.35
        reasons.append(f"recovery_need(error_rate={error_rate:.2f})")

    # ----- Signal 3: Latency spike -----
    # Compare recent 5-min window against a 30-min baseline.
    avg_latency = client.recent_avg_latency()
    baseline_latency = client.recent_avg_latency(window_seconds=WINDOW_SECONDS * 6)
    if avg_latency and baseline_latency and baseline_latency > 0:
        spike_ratio = avg_latency / baseline_latency
        if spike_ratio >= LATENCY_SPIKE_FACTOR:
            latency_score = min(1.0, (spike_ratio - 1.0) / 3.0)
            score += latency_score * 0.2
            reasons.append(f"latency_spike(ratio={spike_ratio:.1f}x)")

    # ----- Signal 4: Starvation -----
    request_count = client.recent_request_count()
    if request_count > 5 and error_rate > 0.5:
        score += 0.15
        reasons.append(f"starvation(requests={request_count},errors={error_rate:.0%})")

    score = min(1.0, score)
    return PrioritySignal(
        score=score,
        boost=score >= BOOST_THRESHOLD,
        reasons=reasons,
    )
