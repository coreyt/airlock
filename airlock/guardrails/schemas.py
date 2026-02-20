"""
Airlock Guardrails — shared data schemas.

Three dataclasses form the contract between the observer, orchestrator,
enforcer, enterprise logger, and slow analyzer:

  GuardrailSignal     — one guardrail's output for a single request
  GuardrailObservation — all signals for a single request
  GuardrailKnobs      — analyzer-tuned weights and thresholds
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GuardrailSignal:
    """Output of a single guardrail scan for one request."""

    guardrail_name: str      # "pii_scan" | "keyword_scan" | "threat_read"
    detected: bool
    score: float             # 0.0 (clean) → 1.0 (certain violation)
    details: dict            # guardrail-specific payload
    duration_ms: float


@dataclass
class GuardrailObservation:
    """Aggregated guardrail signals for a single request."""

    request_id: str | None
    model: str
    client_id: str
    signals: list[GuardrailSignal]
    composite_score: float | None = None       # Phase 2+
    would_block: bool | None = None            # Phase 2+
    orchestrator_version: str | None = None    # Phase 2+


@dataclass
class GuardrailKnobs:
    """Analyzer-tuned weights and thresholds for guardrail evaluation."""

    version: str                                # ISO timestamp of last update
    weights: dict[str, float]                   # guardrail_name → weight
    threshold: float                            # composite score → would_block
    per_guardrail: dict[str, dict] = field(default_factory=dict)


def default_knobs() -> GuardrailKnobs:
    """Return sensible defaults when no knobs file exists."""
    return GuardrailKnobs(
        version="default",
        weights={
            "pii_scan": 0.4,
            "keyword_scan": 0.4,
            "threat_read": 0.2,
        },
        threshold=0.5,
    )
