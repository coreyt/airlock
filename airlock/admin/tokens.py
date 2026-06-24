"""HS256 capability tokens for the Airlock admin / guardrail-skip layer (UN-11).

Zero-infra: tokens are symmetrically signed with a server-side secret
(``AIRLOCK_JWT_SECRET``, or an HMAC-derived key from ``AIRLOCK_MASTER_KEY`` when
that is unset). No database, IdP, or PKI. The operator mints short-lived tokens
with the ``airlock admin mint-token`` CLI; the proxy verifies them.

Scope strings: ``admin:<op>`` (e.g. ``admin:clear_quarantine``) and
``guardrail:skip:<name>`` (e.g. ``guardrail:skip:keyword``). Per CC-11 a
guardrail-skip token's ``sub`` MUST be the client's authenticated key-derived id
(``key:<last8>``); that binding is enforced by the policy decision point, not
here — this module only mints and verifies.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from collections.abc import Iterable, Sequence

import jwt  # PyJWT

ISSUER = "airlock"
ALGORITHM = "HS256"
DEFAULT_LEEWAY_SECONDS = 30


class TokenError(Exception):
    """Raised when a token cannot be minted or fails verification."""


def _derive_from_master(master: str) -> str:
    """HMAC-derive a dedicated admin signing key from the master key.

    Keeps token signing decoupled from the raw master key's other uses while
    needing no extra secret to distribute (HKDF-Extract style: HMAC with a fixed
    context label).
    """
    return hmac.new(
        b"airlock-admin-jwt-v1", master.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _signing_secret() -> str:
    secret = os.getenv("AIRLOCK_JWT_SECRET", "").strip()
    if secret:
        return secret
    master = os.getenv("AIRLOCK_MASTER_KEY", "").strip()
    if not master:
        raise TokenError(
            "no signing secret: set AIRLOCK_JWT_SECRET or AIRLOCK_MASTER_KEY"
        )
    return _derive_from_master(master)


def _prev_secret() -> str | None:
    """Previous secret for rolling rotation (verify-only)."""
    value = os.getenv("AIRLOCK_JWT_SECRET_PREV", "").strip()
    return value or None


def mint_token(
    sub: str,
    scopes: Sequence[str],
    ttl_seconds: int,
    *,
    now: float | None = None,
    jti: str | None = None,
) -> str:
    """Sign a capability token. ``now`` is injectable for testing."""
    if not sub:
        raise TokenError("sub is required")
    if ttl_seconds <= 0:
        raise TokenError("ttl_seconds must be positive")
    issued = int(time.time() if now is None else now)
    claims = {
        "iss": ISSUER,
        "sub": sub,
        "scope": list(scopes),
        "iat": issued,
        "exp": issued + int(ttl_seconds),
        "jti": jti or uuid.uuid4().hex,
    }
    return jwt.encode(claims, _signing_secret(), algorithm=ALGORITHM)


def verify_token(
    token: str,
    *,
    leeway: int = DEFAULT_LEEWAY_SECONDS,
    denylist: Iterable[str] | None = None,
) -> dict:
    """Verify signature, issuer, and expiry; return the claims.

    Tries the current signing secret then the previous one (rolling rotation).
    Raises :class:`TokenError` on any failure. If ``denylist`` is given and the
    token's ``jti`` is in it, the token is rejected (break-glass revocation).
    """
    secrets = [_signing_secret()]
    prev = _prev_secret()
    if prev:
        secrets.append(prev)

    last_err: Exception | None = None
    for secret in secrets:
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                leeway=leeway,
                options={"require": ["exp", "iat", "sub", "iss"]},
            )
        except jwt.PyJWTError as exc:
            last_err = exc
            continue
        jti = claims.get("jti")
        if denylist is not None and jti is not None and jti in set(denylist):
            raise TokenError("token revoked (jti on denylist)")
        return claims
    raise TokenError(f"invalid token: {last_err}")


def token_scopes(claims: dict) -> list[str]:
    scope = claims.get("scope")
    return list(scope) if isinstance(scope, list) else []


def has_scope(claims: dict, scope: str) -> bool:
    return scope in token_scopes(claims)
