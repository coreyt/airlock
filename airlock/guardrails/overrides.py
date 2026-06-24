"""Per-request guardrail skip resolver (Pack 0.5.0-ADM-skip, UN-13).

A trusted client may present a capability token (``X-Airlock-Capability``) whose
``guardrail:skip:<name>`` scopes downgrade specific *content* guards for that
request only. Security invariants:

  * CC-11 — the token's ``sub`` must equal the request's **authenticated**
    key-derived id (``key:<last8>`` of the validated bearer key), NEVER the
    forgeable ``X-Airlock-Client`` attribution header.
  * CC-10 — only content guards are skippable; the breaker / fallbacks are never
    governed here. A skip *downgrades* a guard to its configured ``downgrade_to``
    mode — usually ``observe`` (still scans + logs); a guard may be configured to
    ``off`` (silenced, e.g. ``reasoning_strip``). PII redaction is non-skippable by
    default.
  * Off by default — ``allow_capability_skip: false`` ignores the header entirely.

Content guards call :func:`resolve_guardrail_decision` to obtain a freshly
*verified* decision (the resolver overwrites any value in the client-controllable
``data["metadata"]``, so an injected ``airlock_guardrail_decision`` can never grant
a skip). The verified result is also stamped into metadata for logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_DECISION_KEY = "airlock_guardrail_decision"


@dataclass
class _GuardrailOverrideConfig:
    allow_capability_skip: bool = False
    capability_header: str = "x-airlock-capability"
    # guardrail name -> (skippable, downgrade_to)
    skippable: dict[str, tuple[bool, str]] = field(
        default_factory=lambda: {
            "pii_redact": (False, "observe"),  # never skippable by default
            "keyword": (True, "observe"),
            "response_scan": (True, "observe"),
            "reasoning_strip": (True, "off"),
        }
    )


_cfg = _GuardrailOverrideConfig()


def configure_guardrail_overrides(config: dict | None) -> None:
    """Load the ``guardrail_overrides`` block once at startup (CC-2/CC-3)."""
    global _cfg
    block = (config or {}).get("guardrail_overrides") or {}
    cfg = _GuardrailOverrideConfig(
        allow_capability_skip=bool(block.get("allow_capability_skip", False)),
        capability_header=str(
            block.get("capability_header", "X-Airlock-Capability")
        ).lower(),
    )
    raw = block.get("skippable")
    if isinstance(raw, dict):
        merged = dict(cfg.skippable)
        for name, spec in raw.items():
            if isinstance(spec, dict):
                merged[str(name)] = (
                    bool(spec.get("skippable", False)),
                    str(spec.get("downgrade_to", "observe")),
                )
        cfg.skippable = merged
    _cfg = cfg


def _authenticated_client_id(user_api_key_dict: Any) -> str | None:
    """key:<last8> of the validated bearer key, or None when unauthenticated.

    Deliberately ignores the forgeable X-Airlock-Client header (CC-11).
    """
    key = None
    if user_api_key_dict is not None:
        if hasattr(user_api_key_dict, "api_key"):
            key = user_api_key_dict.api_key
        elif isinstance(user_api_key_dict, dict):
            key = user_api_key_dict.get("api_key")
    if key and len(str(key)) > 8:
        return f"key:{str(key)[-8:]}"
    return None


def _header_value(data: dict, name: str) -> str | None:
    metadata = data.get("metadata") or {}
    for source in (data.get("headers"), metadata.get("headers")):
        if isinstance(source, dict):
            for key, value in source.items():
                if str(key).lower() == name and value:
                    return str(value)
        elif isinstance(source, list):
            for item in source:
                # list of (k, v) pairs, possibly bytes
                try:
                    k, v = item
                except (TypeError, ValueError):
                    continue
                kk = k.decode("utf-8", "replace") if isinstance(k, bytes) else str(k)
                if kk.lower() == name and v:
                    return (
                        v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
                    )
    return None


def resolve_guardrail_decision(data: dict, user_api_key_dict: Any) -> dict[str, str]:
    """Compute (and cache) the per-guardrail effective-mode map for this request.

    Returns only the guardrails that are downgraded; an absent guardrail means
    ``enforce``. Always recomputes and **overwrites**
    ``data["metadata"][_DECISION_KEY]``.

    SECURITY: ``data["metadata"]`` is client-controllable (LiteLLM preserves a
    caller-supplied ``metadata`` dict), so a pre-existing decision key MUST NOT be
    trusted — a client could otherwise inject ``airlock_guardrail_decision`` to
    grant itself skips with no token. We never short-circuit on the existing
    value; the verified result always wins.
    """
    metadata = data.setdefault("metadata", {})
    decision: dict[str, str] = {}
    if not _cfg.allow_capability_skip:
        metadata[_DECISION_KEY] = decision
        return decision

    token = _header_value(data, _cfg.capability_header)
    auth_id = _authenticated_client_id(user_api_key_dict)
    if not token or not auth_id:
        metadata[_DECISION_KEY] = decision
        return decision

    from airlock.admin.tokens import TokenError, token_scopes, verify_token

    try:
        claims = verify_token(token)
    except TokenError:
        metadata[_DECISION_KEY] = decision
        return decision

    # CC-11: bind to the authenticated identity, never the attribution header.
    if str(claims.get("sub")) != auth_id:
        metadata[_DECISION_KEY] = decision
        return decision

    for scope in token_scopes(claims):
        if not scope.startswith("guardrail:skip:"):
            continue
        name = scope[len("guardrail:skip:") :]
        skippable, downgrade_to = _cfg.skippable.get(name, (False, "observe"))
        if skippable:
            decision[name] = downgrade_to

    metadata[_DECISION_KEY] = decision
    return decision


def effective_mode(data: dict, guardrail: str) -> str:
    """Effective mode for a guardrail this request: enforce | observe | off.

    Reads the stamped decision; defaults to ``enforce`` when none was resolved.
    """
    metadata = data.get("metadata") or {}
    decision = metadata.get(_DECISION_KEY) or {}
    return decision.get(guardrail, "enforce")
