"""Bounded representations of upstream provider failures.

Provider exceptions commonly include upstream response bodies.  Those bodies can
contain credentials, request details, or provider-specific diagnostic text, so
they must not cross Airlock's event, state, logging, or tracing boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass


def _status_code(exc: Exception) -> int | None:
    """Extract an HTTP status without consulting the exception message."""
    for value in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


_KNOWN_PROVIDERS = frozenset(
    {
        "anthropic",
        "azure",
        "bedrock",
        "deepseek",
        "gemini",
        "mistral",
        "openai",
        "openrouter",
        "perplexity",
        "vertex_ai",
    }
)


def _provider_name(value: object) -> str | None:
    """Keep provider attribution bounded even for malformed third-party errors."""
    if not isinstance(value, str):
        return None
    normalized = value.lower()
    return normalized if normalized in _KNOWN_PROVIDERS else None


@dataclass(frozen=True)
class ProviderErrorSummary:
    """The only provider-failure detail permitted outside the call boundary."""

    provider: str | None
    error_type: str
    status_code: int | None

    def message(self) -> str:
        parts = ["provider_error", f"type={self.error_type}"]
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return " ".join(parts)


def summarize_provider_error(exc: Exception | None) -> ProviderErrorSummary | None:
    """Return a safe summary only for a LiteLLM-attributed provider error.

    Airlock deliberately leaves local validation/evaluation exceptions unchanged;
    only errors carrying LiteLLM's ``llm_provider`` marker crossed a provider
    boundary and require redaction here.
    """
    if exc is None:
        return None
    raw_provider = getattr(exc, "llm_provider", None)
    if not isinstance(raw_provider, str):
        return None
    provider = _provider_name(raw_provider)
    return ProviderErrorSummary(
        provider=provider,
        error_type=type(exc).__name__,
        status_code=_status_code(exc),
    )
