"""Concurrency / non-blocking tests for the PII guard (UN-27).

These prove that Presidio's synchronous ``analyzer.analyze`` no longer blocks
the asyncio event loop: after the ``asyncio.to_thread`` offload, concurrent
PII-enabled requests run in parallel and a heartbeat keeps ticking while the
(slow, faked) analyzer runs. They use a deterministic ``time.sleep`` fake
analyzer so the offload is observable without depending on real Presidio.
"""

from __future__ import annotations

import asyncio
import time

import airlock.guardrails.pii_guard as pii_mod
from airlock.guardrails.pii_guard import AirlockPIIGuard


class _SlowAnalyzer:
    """Stand-in for Presidio's AnalyzerEngine whose ``analyze`` blocks the
    calling thread for ``delay`` seconds and finds no entities."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def analyze(self, text, entities, language):  # noqa: ARG002
        time.sleep(self.delay)
        return []


def _completion_data() -> dict:
    return {
        "messages": [{"role": "user", "content": "harmless text with no pii"}],
        "model": "claude-sonnet",
    }


async def test_pii_does_not_block_event_loop(
    monkeypatch, mock_cache, mock_user_api_key_dict
):
    """A concurrent heartbeat keeps ticking while the slow analyzer runs —
    proving the event loop is not blocked by the synchronous Presidio call."""
    monkeypatch.setattr(pii_mod, "_analyzer", _SlowAnalyzer(delay=0.2))
    guard = AirlockPIIGuard()

    state = {"ticks": 0, "stop": False}

    async def heartbeat():
        while not state["stop"]:
            await asyncio.sleep(0.01)
            state["ticks"] += 1

    hb = asyncio.create_task(heartbeat())
    # Yield so the heartbeat task actually starts before the hook runs.
    await asyncio.sleep(0)
    await guard.async_pre_call_hook(
        mock_user_api_key_dict, mock_cache, _completion_data(), "completion"
    )
    state["stop"] = True
    await hb

    # A non-blocked loop ticks ~20 times during a 0.2s offloaded call; if the
    # loop were blocked it would tick ~0 times.
    assert state["ticks"] >= 5


async def test_concurrent_pii_requests_do_not_serialize(
    monkeypatch, mock_cache, mock_user_api_key_dict
):
    """N concurrent PII-enabled requests finish in roughly one analyzer
    delay, not N × delay — i.e. they no longer serialize on the event loop."""
    delay = 0.2
    monkeypatch.setattr(pii_mod, "_analyzer", _SlowAnalyzer(delay=delay))
    guard = AirlockPIIGuard()

    n = 5
    start = time.perf_counter()
    await asyncio.gather(
        *(
            guard.async_pre_call_hook(
                mock_user_api_key_dict, mock_cache, _completion_data(), "completion"
            )
            for _ in range(n)
        )
    )
    elapsed = time.perf_counter() - start

    # Serial execution would take ~n * delay = 1.0s; parallel ~delay = 0.2s.
    assert elapsed < delay * n * 0.6
