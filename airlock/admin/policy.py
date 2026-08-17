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

_REMOTE_TUI_SCOPE = "admin:remote_tui"
_REMOTE_TUI_ALLOWED_SCOPES = {
    "admin:read",
    "admin:read_config",
    "admin:clear_quarantine",
}
_FLEET_READ_TUI_SCOPES = {_REMOTE_TUI_SCOPE, "admin:read"}
_REMOTE_TUI_MAX_TTL_SECONDS = 15 * 60

# Deliberately excludes "" — a missing/stripped client address is NOT loopback
# (fail closed; e.g. a Unix-socket reverse proxy forwarding remote traffic).
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@dataclass
class AdminConfig:
    enabled: bool = False
    trust_loopback: bool = True
    allow_insecure_tokens: bool = False
    behind_tls_proxy: bool = False
    remote_tui: bool = False
    fleet_read_tui: bool = False


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
        remote_tui=bool(block.get("remote_tui", False)),
        fleet_read_tui=bool(block.get("fleet_read_tui", False)),
    )
    if cfg.fleet_read_tui and not cfg.remote_tui:
        raise RuntimeError("admin.fleet_read_tui requires admin.remote_tui: true")
    if cfg.remote_tui and host is not None:
        if (
            not cfg.enabled
            or cfg.trust_loopback
            or not tls_enabled
            or cfg.behind_tls_proxy
            or cfg.allow_insecure_tokens
            or host in LOOPBACK_HOSTS
        ):
            raise RuntimeError(
                "admin.remote_tui requires admin.enabled, trust_loopback: false, "
                "native TLS, a non-loopback bind, and neither TLS-proxy nor "
                "insecure-token mode."
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
    auth_context: str = ""


def _master_key() -> str:
    return os.getenv("AIRLOCK_MASTER_KEY", "")


def decide(
    principal: Principal, op_scope: str, *, loopback_only: bool = False
) -> Decision:
    """Authorize an operation. Never raises — returns a Decision with a status.

    Order: Path A (loopback) → master key → capability JWT (scope check).
    The explicit remote-TUI profile intentionally removes the first two paths
    and constrains its JWT capability surface regardless of source address.
    """
    cfg = _admin_config
    if cfg.remote_tui:
        if not principal.bearer:
            return Decision(
                False, 401, "authentication required", actor=principal.actor
            )
        # Do not let the broad inference/master authority become a remote-TUI
        # credential.  It will fail JWT verification below as an invalid token.
        master = _master_key()
        if master and hmac.compare_digest(principal.bearer, master):
            return Decision(False, 403, "master key is not valid for remote TUI")
        from airlock.admin.tokens import (
            TokenError,
            has_scope,
            token_scopes,
            verify_token,
        )

        try:
            claims = verify_token(principal.bearer)
        except TokenError:
            return Decision(
                False, 403, "invalid or expired token", actor=principal.actor
            )
        actor = str(claims.get("sub") or "token")
        issued, expires = claims.get("iat"), claims.get("exp")
        if (
            not isinstance(issued, int)
            or not isinstance(expires, int)
            or expires - issued > _REMOTE_TUI_MAX_TTL_SECONDS
        ):
            return Decision(
                False, 403, "remote TUI token lifetime exceeds 15 minutes", actor=actor
            )
        if not has_scope(claims, _REMOTE_TUI_SCOPE):
            return Decision(
                False, 403, "token missing scope admin:remote_tui", actor=actor
            )
        scopes = token_scopes(claims)
        if cfg.fleet_read_tui and (
            op_scope != "admin:read"
            or len(scopes) != len(_FLEET_READ_TUI_SCOPES)
            or set(scopes) != _FLEET_READ_TUI_SCOPES
        ):
            return Decision(
                False,
                403,
                "fleet read TUI requires exactly admin:remote_tui and admin:read",
                actor=actor,
            )
        if op_scope not in _REMOTE_TUI_ALLOWED_SCOPES:
            return Decision(
                False, 403, "operation is not available to remote TUI", actor=actor
            )
        if not has_scope(claims, op_scope):
            return Decision(False, 403, f"token missing scope {op_scope}", actor=actor)
        return Decision(True, actor=actor, auth_context="remote_tui_jwt")

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
