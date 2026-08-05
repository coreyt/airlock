"""Per-client authorization for paid side services (issue #21, pack C-2).

Tavily, Perplexity, and NewsCatcher consume paid credits on every call. They
reach Airlock by two different paths, and the posture differs accordingly:

===============  =====================  ==========================================
Service          Path                   Authentication today
===============  =====================  ==========================================
Tavily           model entry            ``AIRLOCK_MASTER_KEY`` (LiteLLM virtual key)
Perplexity       model entry            ``AIRLOCK_MASTER_KEY`` (LiteLLM virtual key)
NewsCatcher      MCP server             ``AIRLOCK_MASTER_KEY`` + MCP tool guard
===============  =====================  ==========================================

**All three are already authenticated** — none is reachable without a valid
key. What was missing is *authorization*: any authenticated caller could spend
credits on any of them. This module adds a per-client allowlist over the same
authenticated identity the guardrail chain already derives, enforced at the
enforcement points that already exist.

**Default is unrestricted.** With no configuration, every authenticated client
may reach every service — today's behavior, unchanged. An operator opts in per
service, so this cannot silently start refusing production traffic.

**Deliberately out of scope:** per-service quotas and billing attribution.
Those are the 0.6.x tenant-keys work, where budgets are the theme; building a
second budget system here would be the parallel-infrastructure mistake pack
C-2 was explicitly bounded against. This answers "may this client call it at
all", not "how much has it spent".
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("airlock.paid_services")

#: Substring patterns identifying each paid service, matched case-insensitively
#: against a model name or an MCP tool/server name.
_SERVICE_PATTERNS: dict[str, tuple[str, ...]] = {
    "tavily": ("tavily",),
    "perplexity": ("perplexity", "sonar"),
    "newscatcher": ("newscatcher",),
}

KNOWN_SERVICES = frozenset(_SERVICE_PATTERNS)


@dataclass(frozen=True)
class AuthorizationDecision:
    """Outcome of one authorization check."""

    service: str | None
    allowed: bool
    reason: str

    def as_metadata(self) -> dict[str, object]:
        return {
            "service": self.service,
            "allowed": self.allowed,
            "reason": self.reason,
        }


def classify_service(name: str | None) -> str | None:
    """Return the paid service *name* belongs to, or None.

    Matches a model name (``perplexity-sonar-pro``, ``tavily/web-search``) or
    an MCP tool/server name. Deliberately substring-based: the config exposes
    several aliases per service, and an exact-match table would silently fail
    open the moment an operator adds another one.
    """
    if not name:
        return None
    lowered = str(name).lower()
    for service, patterns in _SERVICE_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return service
    return None


def _allowlist(service: str) -> list[str] | None:
    """Authorized client IDs for *service*, or None when unrestricted."""
    raw = os.getenv(f"AIRLOCK_PAID_SERVICE_ALLOW_{service.upper()}")
    if raw is None:
        return None
    return [entry.strip() for entry in raw.split(",") if entry.strip()]


def enforcement_enabled() -> bool:
    """True when at least one service has an allowlist configured."""
    return any(_allowlist(service) is not None for service in KNOWN_SERVICES)


def authorize(name: str | None, client_id: str | None) -> AuthorizationDecision:
    """Decide whether *client_id* may reach the paid service behind *name*.

    ``client_id`` must be the **authenticated** identity (the key-derived ID
    from ``guardrail_overrides._authenticated_client_id``), never the
    client-supplied ``X-Airlock-Client`` header — that header is forgeable, and
    authorizing on it would let any caller spend another tenant's credits by
    claiming their name.
    """
    service = classify_service(name)
    if service is None:
        return AuthorizationDecision(None, True, "not_a_paid_service")

    allowed = _allowlist(service)
    if allowed is None:
        return AuthorizationDecision(service, True, "unrestricted")

    if not client_id:
        # An allowlist exists but the caller has no authenticated identity.
        # Failing open here would make the allowlist trivially bypassable.
        return AuthorizationDecision(service, False, "unauthenticated")

    if client_id in allowed:
        return AuthorizationDecision(service, True, "allowlisted")

    return AuthorizationDecision(service, False, "not_allowlisted")


def check_or_raise(name: str | None, client_id: str | None) -> AuthorizationDecision:
    """Authorize, raising ``PermissionError`` when refused.

    The message names the service but never the allowlist's contents — telling
    a refused caller which client IDs *are* authorized would leak the tenant
    list to exactly the party that should not have it.
    """
    decision = authorize(name, client_id)
    if not decision.allowed:
        logger.warning(
            "paid_service_denied service=%s client=%s reason=%s",
            decision.service,
            client_id or "<unauthenticated>",
            decision.reason,
        )
        raise PermissionError(
            f"Client is not authorized to use the paid service '{decision.service}'."
        )
    return decision
