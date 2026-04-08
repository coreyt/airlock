"""
Airlock Orchestrator — during_call guardrail with weighted evaluation.

Evolves the observer into a sensory nerve that reads analyzer-tuned knobs,
evaluates guardrail signals against weights, computes a composite score,
and logs what it **would** do — without enforcing.

The orchestrator replaces the observer in config.yaml and imports scan
functions from observer.py to avoid logic duplication.

Key design:
  - _get_knobs() caches in memory with 30-second TTL to avoid disk reads
  - _evaluate() computes weighted average of signal scores
  - composite_score, would_block, orchestrator_version in metadata
  - NEVER raises — observation + evaluation only
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict
from typing import Any

from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.guardrails.observer import collect_signals, _extract_client_id
from airlock.guardrails.schemas import (
    GuardrailKnobs,
    GuardrailObservation,
    GuardrailSignal,
    default_knobs,
)
from airlock.slow.tuner import load_knobs

logger = logging.getLogger("airlock.guardrails.orchestrator")

# ---------------------------------------------------------------------------
# Knobs cache (30-second TTL, thread-safe)
# ---------------------------------------------------------------------------
_cached_knobs: GuardrailKnobs | None = None
_cached_knobs_ts: float = 0.0
_KNOBS_TTL_SECONDS: float = 30.0
_knobs_lock = threading.Lock()


def _get_knobs() -> GuardrailKnobs:
    """Load knobs with 30-second in-memory cache (thread-safe)."""
    global _cached_knobs, _cached_knobs_ts

    with _knobs_lock:
        now = time.monotonic()
        if _cached_knobs is not None and (now - _cached_knobs_ts) < _KNOBS_TTL_SECONDS:
            return _cached_knobs

        knobs = load_knobs()
        if knobs is None:
            knobs = default_knobs()

        _cached_knobs = knobs
        _cached_knobs_ts = now
        return knobs


def _invalidate_knobs_cache() -> None:
    """Force next _get_knobs() to reload. Useful for testing."""
    global _cached_knobs, _cached_knobs_ts
    with _knobs_lock:
        _cached_knobs = None
        _cached_knobs_ts = 0.0


def evaluate(
    signals: list[GuardrailSignal], knobs: GuardrailKnobs
) -> float:
    """Compute weighted average composite score."""
    total_weight = 0.0
    weighted_sum = 0.0
    for signal in signals:
        w = knobs.weights.get(signal.guardrail_name, 0.0)
        weighted_sum += signal.score * w
        total_weight += w
    return weighted_sum / total_weight if total_weight > 0 else 0.0


# ---------------------------------------------------------------------------
# LiteLLM during_call guardrail
# ---------------------------------------------------------------------------
class AirlockOrchestrator(CustomGuardrail):
    """During-call guardrail with weighted evaluation — never blocks."""

    def __init__(self, **kwargs):
        supported_event_hooks = [
            GuardrailEventHooks.during_call,
            GuardrailEventHooks.during_mcp_call,
        ]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    async def async_moderation_hook(
        self,
        data: dict,
        user_api_key_dict: Any,
        call_type: str,
    ) -> None:
        try:
            signals = collect_signals(data, user_api_key_dict, call_type)
            knobs = _get_knobs()
            composite_score = evaluate(signals, knobs)
            would_block = composite_score >= knobs.threshold
            client_id = _extract_client_id(user_api_key_dict)

            observation = GuardrailObservation(
                request_id=data.get("litellm_call_id"),
                model=data.get("model", "unknown"),
                client_id=client_id,
                signals=signals,
                composite_score=round(composite_score, 4),
                would_block=would_block,
                orchestrator_version=knobs.version,
            )
            data.setdefault("metadata", {})["airlock_observation"] = asdict(
                observation
            )

            if would_block:
                logger.info(
                    "would_block score=%.4f threshold=%.4f model=%s client=%s",
                    composite_score,
                    knobs.threshold,
                    observation.model,
                    client_id,
                )
        except Exception:
            # Orchestrator must never fail the request
            logger.exception("orchestrator_error — swallowed")
