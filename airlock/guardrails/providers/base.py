"""Provider seam for semantic injection detection.

A **provider** is one external (or local) service that can answer a single
question: *does this text contain a prompt-injection or jailbreak attempt?*
Providers know nothing about Airlock's classifier registry, selection policy,
or enforcement mode — they translate a text probe into a
:class:`ProviderVerdict` and nothing more.

Airlock composes providers rather than depending on any one of them:
``airlock.guardrails.prompt_injection.ProviderInjectionClassifier`` fans a
single probe out across N providers and aggregates their verdicts, so a
deployment can run Google Model Armor, Azure Prompt Shields, or a self-hosted
detector — or several at once — without touching the guardrail.

Three rules every provider must honor
-------------------------------------
1. **Never report "clean" when you do not know.** A provider that cannot
   produce a verdict (bad credentials, missing dependency, timeout, malformed
   or empty response, misconfigured resource) returns ``detected=None`` with
   ``available=False``. Silence is not safety: a live reproduction of this
   failure is recorded in ``dev/plans/runs/0.5.9-model-armor-access-witness.md``,
   where a misconfigured Model Armor template returned HTTP 200 ``SUCCESS``
   with no verdict at all. Parsed loosely, that response admits every request
   while looking perfectly healthy.
2. **Emit safe metadata only.** Provider identity, resource labels, API
   version, verdict kind — never the probed text, credentials, raw response
   bodies, or request IDs that could carry user data. Everything in
   :attr:`ProviderVerdict.metadata` may reach JSONL and the corpus artifact.
3. **Stay bounded.** Every network call is subject to an explicit timeout and
   surfaces as an error verdict rather than an exception escaping ``inspect``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Availability(str, Enum):
    """Why a provider can or cannot serve verdicts right now."""

    AVAILABLE = "available"
    #: Deliberately switched off or not configured — an expected resting state.
    DISABLED = "disabled"
    #: Configured but not usable: credentials, dependencies, or a resource
    #: whose settings prevent it from returning verdicts.
    UNAVAILABLE = "unavailable"


#: Verdict kinds, mirrored into ``ClassifierResult.label`` by the classifier.
DETECTED = "prompt_injection"
CLEAN = "clean"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of a provider's startup self-check.

    ``detail`` is a short, safe, operator-facing reason — it is written to
    startup diagnostics, so it must never contain credentials or user text.
    """

    provider: str
    availability: Availability
    detail: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.availability is Availability.AVAILABLE


@dataclass(frozen=True)
class ProviderVerdict:
    """One provider's answer about one piece of text.

    ``detected`` is deliberately tri-state:

    ==============  ===========================================
    ``True``        the provider asserts an injection attempt
    ``False``       the provider asserts the text is clean
    ``None``        the provider has no verdict — *not* clean
    ==============  ===========================================

    ``confidence`` is the provider's native confidence label (e.g. Model
    Armor's ``HIGH`` / ``MEDIUM_AND_ABOVE``) carried through verbatim for
    evidence. It is **not** normalized into a number: confidence scales are not
    comparable across providers, and the smoke probe recorded in the access
    witness found benign false positives scoring HIGH while genuine attacks
    scored only MEDIUM_AND_ABOVE. Treat it as an observation, not a ranking.
    """

    provider: str
    detected: bool | None
    duration_ms: float
    available: bool = True
    confidence: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        if not self.available or self.detected is None:
            return UNAVAILABLE
        return DETECTED if self.detected else CLEAN

    @classmethod
    def unavailable(
        cls,
        provider: str,
        *,
        error: str,
        duration_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderVerdict:
        """Build the no-verdict result. Use this for every failure path."""
        return cls(
            provider=provider,
            detected=None,
            available=False,
            duration_ms=duration_ms,
            error=error,
            metadata=metadata or {},
        )


@runtime_checkable
class InjectionProvider(Protocol):
    """Interface implemented by every injection-detection backend."""

    @property
    def name(self) -> str:
        """Stable identifier, e.g. ``model_armor``. Appears in metadata."""
        ...

    def describe(self) -> dict[str, str]:
        """Safe identity of this provider and the resource it targets.

        Retained in corpus evidence so a run can be attributed to an exact
        service and resource revision. Must contain no secrets.
        """
        ...

    async def preflight(self) -> PreflightResult:
        """Check configuration without classifying anything.

        Must not raise. Providers that cannot validate remotely (for example
        when the runtime identity lacks read access to its own resource) should
        report :attr:`Availability.AVAILABLE` with an explanatory ``detail``
        rather than failing closed on a missing diagnostic permission.
        """
        ...

    async def inspect(self, text: str, *, kind: str = "user_prompt") -> ProviderVerdict:
        """Probe ``text``. Must not raise; failures become unavailable verdicts.

        ``kind`` describes the provenance of the text (``user_prompt`` or
        ``mcp_arguments``) so providers with distinct inspection semantics can
        route accordingly.
        """
        ...

    async def aclose(self) -> None:
        """Release any held resources (HTTP clients, sessions)."""
        ...


class Transport(Protocol):
    """Minimal HTTP seam, injected so adapters are testable without network.

    Implementations return ``(status_code, decoded_json)``. A body that is not
    valid JSON must surface as an empty dict rather than an exception — the
    adapter decides what a malformed response means.
    """

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]: ...

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, dict[str, Any]]: ...

    async def aclose(self) -> None: ...
