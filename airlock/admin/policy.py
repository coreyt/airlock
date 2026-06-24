"""Admin policy decision point (PDP) for the control plane (UN-10/UN-11).

Two auth paths, evaluated against startup config:
  * Path A — the request arrives on the loopback interface and ``trust_loopback``
    is on → operator (all ops). The TUI uses this.
  * Path B — a bearer credential: the master key (full admin) or a signed
    capability JWT whose scope covers the operation.

Everything is off by default (``admin.enabled=false`` → routes 404). A startup
fail-closed check refuses to serve bearer-token admin over plaintext on a
non-loopback bind (CC-12).
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", ""}


@dataclass
class AdminConfig:
    enabled: bool = False
    trust_loopback: bool = True
    allow_insecure_tokens: bool = False
    behind_tls_proxy: bool = False


_admin_config = AdminConfig()


def configure_admin(
    config: dict | None,
    *,
    host: str | None = None,
    tls_enabled: bool = False,
) -> None:
    """Load the ``admin`` config block once at startup (CC-2).

    When ``host`` is given, enforce the CC-12 fail-closed check: admin + a
    non-loopback bind + no TLS is refused unless the operator asserts an upstream
    TLS proxy or explicitly allows insecure tokens.
    """
    global _admin_config
    block = (config or {}).get("admin") or {}
    cfg = AdminConfig(
        enabled=bool(block.get("enabled", False)),
        trust_loopback=bool(block.get("trust_loopback", True)),
        allow_insecure_tokens=bool(block.get("allow_insecure_tokens", False)),
        behind_tls_proxy=bool(block.get("behind_tls_proxy", False)),
    )
    if cfg.enabled and host is not None:
        exposed = host not in LOOPBACK_HOSTS
        if (
            exposed
            and not tls_enabled
            and not cfg.behind_tls_proxy
            and not cfg.allow_insecure_tokens
        ):
            raise RuntimeError(
                "admin.enabled on a non-loopback bind without TLS. Set AIRLOCK_SSL_*, "
                "admin.behind_tls_proxy: true, or admin.allow_insecure_tokens: true."
            )
    _admin_config = cfg


def admin_enabled() -> bool:
    return _admin_config.enabled


@dataclass
class Principal:
    loopback: bool = False
    bearer: str | None = None
    actor: str = "unknown"


@dataclass
class Decision:
    allowed: bool
    status: int = 200
    reason: str = ""
    actor: str = "unknown"


def _master_key() -> str:
    return os.getenv("AIRLOCK_MASTER_KEY", "")


def decide(
    principal: Principal, op_scope: str, *, loopback_only: bool = False
) -> Decision:
    """Authorize an operation. Never raises — returns a Decision with a status.

    Order: Path A (loopback) → master key → capability JWT (scope check).
    """
    cfg = _admin_config
    # Path A — loopback operator.
    if principal.loopback and cfg.trust_loopback:
        return Decision(True, actor=principal.actor or "loopback")

    if loopback_only:
        return Decision(
            False,
            403,
            "operation requires loopback (operator) access",
            actor=principal.actor,
        )

    master = _master_key()
    if principal.bearer and master and hmac.compare_digest(principal.bearer, master):
        return Decision(True, actor="master_key")

    if principal.bearer:
        from airlock.admin.tokens import TokenError, has_scope, verify_token

        try:
            claims = verify_token(principal.bearer)
        except TokenError:
            return Decision(
                False, 403, "invalid or expired token", actor=principal.actor
            )
        if has_scope(claims, op_scope):
            return Decision(True, actor=str(claims.get("sub") or "token"))
        return Decision(
            False,
            403,
            f"token missing scope {op_scope}",
            actor=str(claims.get("sub") or "token"),
        )

    return Decision(False, 401, "authentication required", actor=principal.actor)
