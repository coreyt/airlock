"""
Airlock Slow — Guardrail tuner (Dimension 5).

Reads guardrail observations from JSONL logs and computes tuning knobs:
weights, thresholds, and per-guardrail parameters.  The output is written
to ``airlock-knobs.json`` in LOG_DIR for the orchestrator/enforcer to read.

The tuner is the feedback loop that evolves guardrails from binary
block/allow to weighted scoring with adaptive thresholds.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from airlock.guardrails.schemas import GuardrailKnobs, default_knobs

logger = logging.getLogger("airlock.slow.tuner")


def _log_dir() -> Path:
    return Path(os.getenv("AIRLOCK_LOG_DIR", "./logs"))


KNOBS_FILENAME = "airlock-knobs.json"


def tune_guardrails(records: list[dict]) -> GuardrailKnobs:
    """Analyze guardrail observations and produce tuning knobs."""
    observations = _extract_observations(records)
    if not observations:
        return default_knobs()

    detection_rates = _compute_detection_rates(observations)
    outcome_correlations = _compute_outcome_correlations(records, observations)
    weights = _compute_weights(detection_rates, outcome_correlations)
    threshold = _compute_threshold(observations, weights)
    per_guardrail = _compute_per_guardrail(observations)

    return GuardrailKnobs(
        version=datetime.utcnow().isoformat() + "Z",
        weights=weights,
        threshold=threshold,
        per_guardrail=per_guardrail,
    )


def write_knobs(knobs: GuardrailKnobs, directory: Path | None = None) -> Path:
    """Write airlock-knobs.json to the specified or default directory."""
    target_dir = directory or _log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / KNOBS_FILENAME
    path.write_text(json.dumps(asdict(knobs), indent=2) + "\n")
    logger.info("knobs_written path=%s version=%s", path, knobs.version)
    return path


def load_knobs(directory: Path | None = None) -> GuardrailKnobs | None:
    """Read airlock-knobs.json. Returns None if missing."""
    target_dir = directory or _log_dir()
    path = target_dir / KNOBS_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return GuardrailKnobs(
            version=data["version"],
            weights=data["weights"],
            threshold=data["threshold"],
            per_guardrail=data.get("per_guardrail", {}),
        )
    except (json.JSONDecodeError, KeyError):
        logger.warning("knobs_load_failed path=%s", path)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _extract_observations(records: list[dict]) -> list[dict]:
    """Pull airlock_observation dicts from log records."""
    observations = []
    for r in records:
        obs = r.get("airlock_observation")
        if obs and isinstance(obs, dict):
            observations.append(obs)
    return observations


def _compute_detection_rates(
    observations: list[dict],
) -> dict[str, float]:
    """Per-guardrail detection rate: how often does it fire?"""
    counts: dict[str, int] = defaultdict(int)
    totals: dict[str, int] = defaultdict(int)

    for obs in observations:
        for signal in obs.get("signals", []):
            name = signal.get("guardrail_name", "")
            totals[name] += 1
            if signal.get("detected"):
                counts[name] += 1

    return {name: counts[name] / totals[name] for name in totals if totals[name] > 0}


def _compute_outcome_correlations(
    records: list[dict], observations: list[dict]
) -> dict[str, float]:
    """Correlate guardrail detections with request failure.

    Higher correlation → the guardrail detects things that cause failures
    → it should have a higher weight.
    """
    # Build a map from request_id → success
    outcome_map: dict[str, bool] = {}
    for r in records:
        rid = r.get("request_id")
        if rid is not None:
            outcome_map[rid] = r.get("success", True)

    signal_outcomes: dict[str, dict[str, int]] = defaultdict(
        lambda: {"detected_failed": 0, "detected_total": 0, "total": 0}
    )

    for obs in observations:
        rid = obs.get("request_id")
        success = outcome_map.get(rid, True)
        for signal in obs.get("signals", []):
            name = signal.get("guardrail_name", "")
            signal_outcomes[name]["total"] += 1
            if signal.get("detected"):
                signal_outcomes[name]["detected_total"] += 1
                if not success:
                    signal_outcomes[name]["detected_failed"] += 1

    correlations: dict[str, float] = {}
    for name, stats in signal_outcomes.items():
        if stats["detected_total"] > 0:
            # Proportion of detections that correspond to failures
            correlations[name] = stats["detected_failed"] / stats["detected_total"]
        else:
            correlations[name] = 0.0

    return correlations


def _compute_weights(
    detection_rates: dict[str, float],
    correlations: dict[str, float],
) -> dict[str, float]:
    """Compute weights from detection rates and outcome correlations.

    High detection rate with low correlation → noisy → lower weight.
    High correlation → signal is meaningful → higher weight.
    """
    defaults = default_knobs()
    all_names = set(defaults.weights) | set(detection_rates) | set(correlations)
    raw: dict[str, float] = {}

    for name in all_names:
        base = defaults.weights.get(name, 0.3)
        rate = detection_rates.get(name, 0.0)
        corr = correlations.get(name, 0.0)

        # Noisy penalty: if detection rate > 50%, reduce weight
        noise_factor = max(0.3, 1.0 - rate) if rate > 0.5 else 1.0
        # Correlation boost: higher correlation → boost weight
        corr_factor = 1.0 + corr

        raw[name] = base * noise_factor * corr_factor

    # Normalize so weights sum to 1.0
    total = sum(raw.values())
    if total > 0:
        return {name: round(w / total, 4) for name, w in raw.items()}
    return defaults.weights


def _compute_threshold(observations: list[dict], weights: dict[str, float]) -> float:
    """Set threshold from observed composite score distribution.

    Defaults to 0.5 with few observations.  With enough data, uses the
    90th percentile of composite scores so only clear outliers would block.
    """
    if len(observations) < 10:
        return 0.5

    scores = []
    for obs in observations:
        score = _weighted_score(obs.get("signals", []), weights)
        scores.append(score)

    scores.sort()
    p90_idx = int(len(scores) * 0.9)
    p90 = scores[min(p90_idx, len(scores) - 1)]
    # Threshold is at least 0.3, at most 0.9
    return round(max(0.3, min(0.9, p90)), 4)


def _weighted_score(signals: list[dict], weights: dict[str, float]) -> float:
    """Compute weighted average score for a set of signals."""
    total_weight = 0.0
    weighted_sum = 0.0
    for signal in signals:
        name = signal.get("guardrail_name", "")
        w = weights.get(name, 0.0)
        weighted_sum += signal.get("score", 0.0) * w
        total_weight += w
    return weighted_sum / total_weight if total_weight > 0 else 0.0


def _compute_per_guardrail(
    observations: list[dict],
) -> dict[str, dict[str, Any]]:
    """Compute per-guardrail tuning hints."""
    pii_entity_counts: dict[str, int] = defaultdict(int)

    for obs in observations:
        for signal in obs.get("signals", []):
            if signal.get("guardrail_name") == "pii_scan":
                entities = signal.get("details", {}).get("entities", {})
                for entity_type, count in entities.items():
                    pii_entity_counts[entity_type] += count

    result: dict[str, dict[str, Any]] = {}
    if pii_entity_counts:
        result["pii_scan"] = {
            "entity_frequency": dict(pii_entity_counts),
            "total_detections": sum(pii_entity_counts.values()),
        }

    return result
