"""Shared classifier contract types.

These live in a neutral module rather than in :mod:`airlock.guardrails.semantic`
for an import-identity reason. LiteLLM loads custom guardrails via
``importlib.util.spec_from_file_location``, which can produce a *second* module
object for ``semantic.py`` distinct from the package-imported one. Dataclasses
defined there would then exist as two unrelated classes, and the orchestrator's
``isinstance(meta, ClassifierMetadata)`` selection check would silently fail —
quietly downgrading adaptive selection to "run everything".

Defining them here, where every importer resolves through the normal package
path, keeps exactly one class identity. :mod:`airlock.guardrails.semantic`
re-exports these names, so existing importers are unaffected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


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


@dataclass(frozen=True)
class ClassifierMetadata:
    """Explicit opt-in metadata used by adaptive selection."""

    tags: frozenset[str] = frozenset()
    content_types: frozenset[str] = frozenset({"text"})
    cost_class: str = "heavy"  # light | heavy
    min_content_length: int = 0


class Classifier(Protocol):
    """Interface that pluggable classifiers must satisfy.

    Classifiers can be sync or async — the orchestrator wraps sync callables
    in ``asyncio.to_thread`` automatically.
    """

    @property
    def name(self) -> str: ...

    async def classify(self, text: str) -> ClassifierResult: ...


@dataclass
class OrchestratorVerdict:
    """Aggregate result from running all registered classifiers."""

    blocked: bool
    blocking_classifier: str | None  # name of the classifier that triggered the block
    results: list[ClassifierResult]
    total_duration_ms: float
    selected: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    short_circuited: list[dict[str, str]] = field(default_factory=list)


def fail_open() -> bool:
    """Should classifier errors be treated as pass (True) or block (False)?"""
    return os.getenv("AIRLOCK_SEMANTIC_BLOCK_ON_FAIL", "pass").lower() != "block"
