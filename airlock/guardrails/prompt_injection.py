"""Two-tier input prompt-injection classifiers.

Tier 1 — :class:`InputInjectionTripwire`: local, deterministic, no network. It
catches unambiguous known attack forms and, under adaptive selection, a
positive verdict short-circuits the remote tier.

Tier 2 — :class:`ProviderInjectionClassifier`: semantic detection delegated to
one or more :class:`~airlock.guardrails.providers.base.InjectionProvider`
backends, fanned out concurrently and aggregated. The classifier holds the
policy (which providers, how to combine them, what an absent verdict means);
providers hold only the mechanics of talking to a service.

Neither classifier decides what happens to a request. They produce verdicts;
:mod:`airlock.guardrails.semantic` applies the ``observe``/``shadow``/``enforce``
mode. That separation is why an observed detection cannot be mistaken for a
blocked request.

Neither records matched text. The tripwire reports category names, the provider
tier reports provider identity and verdict kind — enough to investigate a false
positive without copying user content into logs or corpus artifacts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Iterable, Sequence

from .classifier_types import ClassifierMetadata, ClassifierResult, fail_open
from .providers.base import InjectionProvider, ProviderVerdict

logger = logging.getLogger("airlock.guardrails.prompt_injection")

TRIPWIRE_NAME = "input_injection_tripwire"
PROVIDER_CLASSIFIER_NAME = "model_armor_prompt_injection"

_THRESHOLD = 0.5
LABEL_INJECTION = "prompt_injection"
LABEL_CLEAN = "clean"

#: Outer bound on a single provider probe, independent of the provider's own
#: timeout. Providers are contracted to bound themselves; this catches the
#: adapter that does not, so a hung backend cannot stall the request path.
_PROVIDER_HARD_TIMEOUT_SECONDS = 10.0

#: Deterministic patterns for attack forms that are unambiguous in isolation.
#: Deliberately narrow: this tier short-circuits the semantic tier under
#: adaptive selection, so a loose pattern here suppresses the better classifier.
#: Category names — never matched text — are what reaches metadata.
_TRIPWIRE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}?\b"
            r"(?:all\s+)?(?:previous|prior|earlier|above|preceding)\b[^.\n]{0,20}?\b"
            r"(?:instruction|instructions|prompt|prompts|rule|rules|direction|directions)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_exfiltration",
        re.compile(
            r"\b(?:reveal|show|print|repeat|output|disclose|dump)\b[^.\n]{0,40}?\b"
            r"(?:system|initial|original|hidden)\s+(?:prompt|instructions|message)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_play_jailbreak",
        re.compile(
            r"\b(?:you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on\s+you)\b"
            r"[^.\n]{0,60}?\b(?:DAN|do\s+anything\s+now|unrestricted|no\s+restrictions|"
            r"without\s+(?:any\s+)?(?:policy|policies|filter|filters|limits|limitations|"
            r"restrictions))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "developer_mode_claim",
        re.compile(
            r"\b(?:developer|debug|god|sudo|root|admin)\s+mode\b[^.\n]{0,30}?\b"
            r"(?:enabled|activated|on|engage)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "guardrail_disable_request",
        re.compile(
            r"\b(?:disable|bypass|turn\s+off|switch\s+off|remove)\b[^.\n]{0,30}?\b"
            r"(?:safety|guardrail|guardrails|filter|filters|content\s+policy|"
            r"moderation|restrictions)\b",
            re.IGNORECASE,
        ),
    ),
)


class InputInjectionTripwire:
    """Local deterministic tier. Fast, offline, and intentionally conservative.

    Reports the categories that matched. Never the text that matched them.
    """

    metadata = ClassifierMetadata(
        tags=frozenset({"prompt_injection"}),
        content_types=frozenset({"text"}),
        cost_class="light",
        # No min_content_length: short attacks ("ignore previous instructions")
        # are exactly the case a length-based skip would miss.
        min_content_length=0,
    )

    def __init__(self, patterns: Sequence[tuple[str, re.Pattern[str]]] | None = None):
        self._patterns = tuple(patterns) if patterns is not None else _TRIPWIRE_PATTERNS

    @property
    def name(self) -> str:
        return TRIPWIRE_NAME

    def categories(self, text: str) -> list[str]:
        """Category names matching ``text``, in declaration order."""
        return [name for name, pattern in self._patterns if pattern.search(text)]

    async def classify(self, text: str) -> ClassifierResult:
        started = time.monotonic()
        matched = self.categories(text)
        duration_ms = (time.monotonic() - started) * 1000
        return ClassifierResult(
            name=self.name,
            score=1.0 if matched else 0.0,
            threshold=_THRESHOLD,
            blocked=bool(matched),
            label=LABEL_INJECTION if matched else LABEL_CLEAN,
            duration_ms=duration_ms,
            metadata={"tier": "light", "categories": matched},
        )


#: Why a classifier had no verdict. Recorded per result and used to select the
#: unavailability policy, because the causes have different threat models.
REASON_RATE_LIMIT = "rate_limit"
REASON_TIMEOUT = "timeout"
REASON_AUTH = "auth"
REASON_MISCONFIGURED = "misconfigured"
REASON_TRANSPORT = "transport"
REASON_NO_PROVIDER = "no_provider"
REASON_UNKNOWN = "unknown"


def classify_unavailable_reason(error: str | None) -> str:
    """Map a provider error string onto a coarse cause.

    The distinction that matters is **rate limit versus everything else**: quota
    exhaustion is the only failure an attacker can deliberately induce, by
    flooding the proxy until the provider budget is gone and the classifier
    stops answering.
    """
    if not error:
        return REASON_UNKNOWN
    first = error.split(";")[0].strip()
    if first in ("local_rate_limit", "http_429"):
        return REASON_RATE_LIMIT
    if first in ("timeout", "provider_hard_timeout"):
        return REASON_TIMEOUT
    if first in ("http_401", "http_403"):
        return REASON_AUTH
    if first in (
        "no_filter_results",
        "pi_and_jailbreak_filter_absent",
    ) or first.startswith(("invocation_result:", "execution_state:", "match_state:")):
        return REASON_MISCONFIGURED
    if first.startswith(("transport_error:", "provider_exception:")):
        return REASON_TRANSPORT
    if first == "no_providers_configured":
        return REASON_NO_PROVIDER
    return REASON_UNKNOWN


def _policy_for(reason: str, env: dict[str, str] | None = None) -> str:
    """Resolve the allow/block policy for an unavailable verdict.

    ``AIRLOCK_SEMANTIC_ON_RATE_LIMIT`` overrides
    ``AIRLOCK_SEMANTIC_ON_UNAVAILABLE`` for quota exhaustion only. Both default
    to allow (fail open), and the legacy ``AIRLOCK_SEMANTIC_BLOCK_ON_FAIL``
    still forces block when set, so existing deployments keep their behavior.

    Note this only bites in ``enforce`` mode — in observe and shadow the guard
    never raises regardless of policy.
    """
    source = os.environ if env is None else env

    def _read(name: str) -> str | None:
        raw = (source.get(name) or "").strip().lower()
        return raw if raw in ("allow", "block") else None

    if reason == REASON_RATE_LIMIT:
        specific = _read("AIRLOCK_SEMANTIC_ON_RATE_LIMIT")
        if specific:
            return specific
    general = _read("AIRLOCK_SEMANTIC_ON_UNAVAILABLE")
    if general:
        return general
    return "allow" if fail_open() else "block"


def _aggregate(verdicts: Sequence[ProviderVerdict], policy: str) -> bool:
    """Combine usable provider verdicts under ``policy``.

    Callers must filter to usable verdicts first — an unavailable provider has
    no opinion and must not be counted as a vote either way.
    """
    detections = sum(1 for verdict in verdicts if verdict.detected)
    if policy == "all":
        return detections == len(verdicts)
    if policy == "majority":
        return detections * 2 > len(verdicts)
    return detections > 0  # "any" — the default


class ProviderInjectionClassifier:
    """Semantic tier backed by one or more injection providers.

    Aggregation policies (``AIRLOCK_INJECTION_AGGREGATION``):

    ``any`` (default)
        Detect if any provider detects. Highest recall; a single
        false-positive-prone provider drives the verdict.
    ``all``
        Detect only on unanimity among providers that answered.
    ``majority``
        Detect on a strict majority of providers that answered.

    Unavailability is never a vote. If no provider returns a usable verdict the
    classifier reports an error result rather than a clean one, and honors
    ``AIRLOCK_SEMANTIC_BLOCK_ON_FAIL`` for whether that error blocks.
    """

    metadata = ClassifierMetadata(
        tags=frozenset({"prompt_injection"}),
        content_types=frozenset({"text"}),
        cost_class="heavy",
        min_content_length=0,
    )

    def __init__(
        self,
        providers: Iterable[InjectionProvider],
        *,
        name: str = PROVIDER_CLASSIFIER_NAME,
        aggregation: str | None = None,
        request_kind: str = "user_prompt",
        hard_timeout: float = _PROVIDER_HARD_TIMEOUT_SECONDS,
    ) -> None:
        self._providers = list(providers)
        self._name = name
        policy = (
            aggregation
            or os.getenv("AIRLOCK_INJECTION_AGGREGATION", "any").strip().lower()
        )
        self._aggregation = policy if policy in ("any", "all", "majority") else "any"
        self._request_kind = request_kind
        self._hard_timeout = max(0.1, float(hard_timeout))

    @property
    def name(self) -> str:
        return self._name

    @property
    def providers(self) -> tuple[InjectionProvider, ...]:
        return tuple(self._providers)

    def describe(self) -> dict[str, object]:
        """Safe identity of the classifier and every backing provider."""
        return {
            "classifier": self._name,
            "aggregation": self._aggregation,
            "providers": [provider.describe() for provider in self._providers],
        }

    async def preflight(self) -> list:
        """Preflight every provider; used by startup diagnostics."""
        if not self._providers:
            return []
        return list(await asyncio.gather(*(p.preflight() for p in self._providers)))

    async def classify(self, text: str, *, kind: str | None = None) -> ClassifierResult:
        started = time.monotonic()
        request_kind = kind or self._request_kind

        if not self._providers:
            return self._error_result(
                "no_providers_configured", (time.monotonic() - started) * 1000, []
            )

        verdicts: list[ProviderVerdict] = list(
            await asyncio.gather(
                *(
                    self._safe_inspect(provider, text, request_kind, self._hard_timeout)
                    for provider in self._providers
                )
            )
        )
        duration_ms = (time.monotonic() - started) * 1000
        usable = [v for v in verdicts if v.available and v.detected is not None]
        per_provider = [self._provider_metadata(v) for v in verdicts]

        if not usable:
            errors = sorted({v.error or "unavailable" for v in verdicts})
            return self._error_result(
                ";".join(errors), duration_ms, per_provider, request_kind
            )

        detected = _aggregate(usable, self._aggregation)
        # Confidence labels are provider-native and not comparable across
        # providers, so they are carried per provider rather than merged.
        return ClassifierResult(
            name=self._name,
            score=1.0 if detected else 0.0,
            threshold=_THRESHOLD,
            blocked=detected,
            label=LABEL_INJECTION if detected else LABEL_CLEAN,
            duration_ms=duration_ms,
            metadata={
                "tier": "heavy",
                "aggregation": self._aggregation,
                "request_kind": request_kind,
                "providers_total": len(verdicts),
                "providers_answered": len(usable),
                "provider_results": per_provider,
            },
        )

    # -- internals ---------------------------------------------------------
    @staticmethod
    async def _safe_inspect(
        provider: InjectionProvider, text: str, kind: str, hard_timeout: float
    ) -> ProviderVerdict:
        """Isolate each provider: one failing backend cannot mute the others.

        The ``hard_timeout`` is defence in depth. Providers are contracted to
        bound their own calls, but this classifier runs on the request path and
        a third-party adapter that regresses — or simply forgets — must not be
        able to hang the guard. Its own timeout should fire first; this one only
        catches the case where it did not.
        """
        name = getattr(provider, "name", "unknown")
        try:
            return await asyncio.wait_for(
                provider.inspect(text, kind=kind), timeout=hard_timeout
            )
        except asyncio.TimeoutError:
            logger.error(
                "injection_provider_hung name=%s limit=%.1fs", name, hard_timeout
            )
            return ProviderVerdict.unavailable(name, error="provider_hard_timeout")
        except Exception as exc:  # noqa: BLE001 - provider bugs must stay contained
            logger.error(
                "injection_provider_raised name=%s error_type=%s",
                name,
                type(exc).__name__,
            )
            return ProviderVerdict.unavailable(
                name,
                error=f"provider_exception:{type(exc).__name__}",
            )

    @staticmethod
    def _provider_metadata(verdict: ProviderVerdict) -> dict[str, object]:
        entry: dict[str, object] = {
            "provider": verdict.provider,
            "verdict": verdict.label,
            "duration_ms": round(verdict.duration_ms, 2),
        }
        if verdict.confidence:
            entry["confidence"] = verdict.confidence
        if verdict.error:
            entry["error"] = verdict.error
        if verdict.metadata:
            entry.update({k: v for k, v in verdict.metadata.items() if k not in entry})
        return entry

    def _error_result(
        self,
        error: str,
        duration_ms: float,
        per_provider: list,
        request_kind: str | None = None,
    ) -> ClassifierResult:
        """An unavailable classifier — explicitly not a clean verdict."""
        reason = classify_unavailable_reason(error)
        policy = _policy_for(reason)
        blocked = policy == "block"
        metadata: dict[str, object] = {
            "tier": "heavy",
            "aggregation": self._aggregation,
            "providers_total": len(self._providers),
            "providers_answered": 0,
            "provider_results": per_provider,
            "unavailable_reason": reason,
            "unavailable_policy": policy,
        }
        if request_kind:
            metadata["request_kind"] = request_kind
        return ClassifierResult(
            name=self._name,
            score=1.0 if blocked else 0.0,
            threshold=_THRESHOLD,
            blocked=blocked,
            label="unavailable",
            duration_ms=duration_ms,
            error=error,
            metadata=metadata,
        )

    async def aclose(self) -> None:
        for provider in self._providers:
            try:
                await provider.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "injection_provider_close_failed name=%s error=%s",
                    getattr(provider, "name", "unknown"),
                    type(exc).__name__,
                )


def build_classifiers(env: dict[str, str] | None = None) -> list:
    """Build the built-in classifiers this deployment has enabled.

    The tripwire is local and on by default. The provider tier appears only
    when at least one provider is configured, so a deployment with no semantic
    backend runs the deterministic tier alone rather than a permanently
    unavailable remote classifier.
    """
    from .providers.registry import build_providers

    source = os.environ if env is None else env
    classifiers: list = []

    tripwire_flag = source.get("AIRLOCK_INJECTION_TRIPWIRE_ENABLED", "true")
    if tripwire_flag.strip().lower() not in ("0", "false", "no", "off"):
        classifiers.append(InputInjectionTripwire())

    providers = build_providers(env)
    if providers:
        classifiers.append(ProviderInjectionClassifier(providers))
    return classifiers
