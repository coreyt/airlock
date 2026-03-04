"""
Airlock Semantic Guard — thin orchestration layer for ML-based classifiers.

Runs as a ``during_call`` guardrail: executes **in parallel with the LLM call**
so classifier latency is hidden behind the (typically slower) provider round-trip.

Design goals
------------
1. **Pluggable classifiers** — any callable that accepts text and returns a
   ``ClassifierResult`` can be registered.  Classifiers are run concurrently
   via ``asyncio.gather``.
2. **Observation first** — every classifier verdict is logged to request
   metadata so the enterprise logger and slow analyzer can see scores,
   latencies, and decisions.  This data is the foundation for learning
   escalation thresholds before hardcoding them.
3. **Simple aggregation** — block only when *any* classifier exceeds its own
   confidence threshold.  No cross-classifier score blending yet; that comes
   after we have real data from the observation logs.

Env vars
--------
    AIRLOCK_SEMANTIC_BLOCK_ON_FAIL
        What to do when a classifier itself errors (import failure, timeout,
        etc.).  ``"block"`` rejects the request; ``"pass"`` (default) logs
        the error and allows the request through.  Fail-open is the safe
        default for a new system where false positives are the bigger risk.

Registration in config.yaml
---------------------------
    guardrails:
      - guardrail_name: airlock-semantic-guard
        litellm_params:
          guardrail: airlock.guardrails.semantic
          mode: during_call
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# LiteLLM loads custom guardrails via importlib.util.spec_from_file_location
# without registering the module in sys.modules.  Python 3.10's @dataclass
# needs the module there to resolve type annotations.
sys.modules.setdefault(__name__, type(sys)(__name__))

from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from .extract import extract_text as _extract_text_unified

logger = logging.getLogger("airlock.guardrails.semantic")


# ---------------------------------------------------------------------------
# Classifier protocol & result type
# ---------------------------------------------------------------------------
@dataclass
class ClassifierResult:
    """Verdict from a single classifier run."""

    name: str  # e.g. "prompt_injection", "topic_filter"
    score: float  # 0.0 (safe) → 1.0 (violation)
    threshold: float  # score >= threshold → block
    blocked: bool  # convenience: score >= threshold
    label: str  # human-readable label, e.g. "injection", "off_topic"
    duration_ms: float  # wall-clock time for this classifier
    error: str | None = None  # non-None if the classifier itself failed
    metadata: dict[str, Any] = field(default_factory=dict)  # classifier-specific extras


class Classifier(Protocol):
    """Interface that pluggable classifiers must satisfy.

    Classifiers can be sync or async — the orchestrator wraps sync callables
    in ``asyncio.to_thread`` automatically.
    """

    @property
    def name(self) -> str: ...

    async def classify(self, text: str) -> ClassifierResult: ...


# ---------------------------------------------------------------------------
# Orchestrator verdict (aggregate of all classifiers)
# ---------------------------------------------------------------------------
@dataclass
class OrchestratorVerdict:
    """Aggregate result from running all registered classifiers."""

    blocked: bool
    blocking_classifier: str | None  # name of the classifier that triggered the block
    results: list[ClassifierResult]
    total_duration_ms: float


# ---------------------------------------------------------------------------
# Orchestrator core
# ---------------------------------------------------------------------------
def _fail_open() -> bool:
    """Should classifier errors be treated as pass (True) or block (False)?"""
    return os.getenv("AIRLOCK_SEMANTIC_BLOCK_ON_FAIL", "pass").lower() != "block"


async def run_classifiers(
    classifiers: list[Classifier],
    text: str,
) -> OrchestratorVerdict:
    """Run all classifiers concurrently and aggregate results.

    Each classifier is isolated: if one raises, it is recorded as an errored
    result and does not prevent the others from completing.
    """
    start = time.monotonic()

    async def _safe_run(classifier: Classifier) -> ClassifierResult:
        t0 = time.monotonic()
        try:
            result = await classifier.classify(text)
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "classifier_error name=%s error=%s",
                classifier.name,
                exc,
            )
            return ClassifierResult(
                name=classifier.name,
                score=1.0 if not _fail_open() else 0.0,
                threshold=0.5,
                blocked=not _fail_open(),
                label="error",
                duration_ms=duration_ms,
                error=str(exc),
            )

    results = await asyncio.gather(*[_safe_run(c) for c in classifiers])

    total_duration_ms = (time.monotonic() - start) * 1000
    results_list = list(results)

    # Find first blocking result (if any)
    blocking_classifier = None
    for r in results_list:
        if r.blocked:
            blocking_classifier = r.name
            break

    return OrchestratorVerdict(
        blocked=blocking_classifier is not None,
        blocking_classifier=blocking_classifier,
        results=results_list,
        total_duration_ms=total_duration_ms,
    )


# ---------------------------------------------------------------------------
# Classifier registry
# ---------------------------------------------------------------------------
# Module-level list — classifiers register themselves here at import time
# or are added programmatically.  Starts empty; classifiers are added as
# they are implemented (Steps 2 & 3).
_classifiers: list[Classifier] = []


def register_classifier(classifier: Classifier) -> None:
    """Add a classifier to the orchestrator's registry."""
    _classifiers.append(classifier)
    logger.info("classifier_registered name=%s", classifier.name)


def clear_classifiers() -> None:
    """Remove all registered classifiers (primarily for testing)."""
    _classifiers.clear()


def registered_classifiers() -> list[Classifier]:
    """Return a copy of the current classifier list."""
    return list(_classifiers)


# ---------------------------------------------------------------------------
# LiteLLM guardrail
# ---------------------------------------------------------------------------
class AirlockSemanticGuard(CustomGuardrail):
    """During-call guardrail that orchestrates ML classifiers in parallel.

    Runs concurrently with the LLM API call.  Attaches all classifier
    verdicts to request metadata for downstream logging and analysis.
    If any classifier blocks, raises ``ValueError`` to reject the request.
    """

    def __init__(self, **kwargs: Any) -> None:
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
        classifiers = registered_classifiers()
        if not classifiers:
            # No classifiers registered yet — nothing to do.
            # Still attach empty metadata so logs show the guard ran.
            metadata = data.setdefault("metadata", {})
            metadata["airlock_semantic"] = {
                "status": "no_classifiers",
                "results": [],
                "total_duration_ms": 0.0,
            }
            return

        text = _extract_text_unified(data, call_type)
        if not text.strip():
            return

        verdict = await run_classifiers(classifiers, text)

        # Always attach verdict to metadata — this is the learning signal
        metadata = data.setdefault("metadata", {})
        metadata["airlock_semantic"] = {
            "status": "blocked" if verdict.blocked else "passed",
            "blocking_classifier": verdict.blocking_classifier,
            "total_duration_ms": round(verdict.total_duration_ms, 2),
            "results": [
                {
                    "name": r.name,
                    "score": round(r.score, 4),
                    "threshold": r.threshold,
                    "blocked": r.blocked,
                    "label": r.label,
                    "duration_ms": round(r.duration_ms, 2),
                    "error": r.error,
                    **({"metadata": r.metadata} if r.metadata else {}),
                }
                for r in verdict.results
            ],
        }

        # Log every run — this is the data we're collecting
        for r in verdict.results:
            if r.error:
                logger.warning(
                    "classifier_result name=%s label=%s score=%.4f "
                    "threshold=%.2f blocked=%s error=%s duration_ms=%.1f",
                    r.name, r.label, r.score, r.threshold,
                    r.blocked, r.error, r.duration_ms,
                )
            else:
                logger.info(
                    "classifier_result name=%s label=%s score=%.4f "
                    "threshold=%.2f blocked=%s duration_ms=%.1f",
                    r.name, r.label, r.score, r.threshold,
                    r.blocked, r.duration_ms,
                )

        if verdict.blocked:
            logger.warning(
                "semantic_blocked classifier=%s total_duration_ms=%.1f",
                verdict.blocking_classifier,
                verdict.total_duration_ms,
            )
            raise ValueError(
                "Request blocked by content policy. "
                "Please revise your prompt and try again."
            )
