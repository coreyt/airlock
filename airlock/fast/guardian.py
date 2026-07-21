"""
Airlock Fast Guardian — LiteLLM pre_call guardrail.

This is the **reactive mechanism** for the fast subsystem.  It runs on
every inbound request and, in a single pass, performs three checks:

  1. Threat gate   — is this client in back-off or triggering attack
                     heuristics?  If so, reject immediately.
  2. Circuit break — is the requested model's circuit open?  If so,
                     transparently swap to a healthy fallback model.
  3. Priority tag  — compute a priority score and attach it as request
                     metadata so downstream routing / queue logic can
                     give speed-bursts to clients that need them.

Registered in config.yaml as a pre_call guardrail:

    guardrails:
      - guardrail_name: airlock-fast-guardian
        litellm_params:
          guardrail: airlock.fast.guardian
          mode: pre_call
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.callbacks.enterprise_logger import write_precall_block_record
from airlock.proxy_errors import AirlockProviderBlocked, sanitize_reason
from airlock.reasoning_effort import normalize_reasoning_effort
from airlock.transparency import detect_dropped_params, record_mutation
from airlock.client_identity import (
    client_id_from_api_key,
    extract_airlock_client_from_request,
)
from airlock.gemini_interface import apply_gemini_request_semantics
from airlock.text_extract import extract_text, is_batch_call, is_mcp_call

from . import admission as _admission_mod
from .circuit_breaker import check_model_with_filters
from .model_alias import alias_table
from .priority import compute_priority
from .router import apply_routing, infer_provider
from .state import store
from .threat_detector import assess_threat

logger = logging.getLogger("airlock.fast.guardian")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_client_id(user_api_key_dict: Any) -> str:
    """Derive a stable client identifier from the API-key metadata.

    Thin delegator to the canonical ``client_identity`` implementation; kept by
    name for back-compat with importers.
    """
    return client_id_from_api_key(user_api_key_dict)


def _request_client_id(data: dict[str, Any], user_api_key_dict: Any) -> str:
    """Prefer the inbound Airlock client header for request metadata.

    Thin delegator to the canonical ``client_identity`` implementation.
    """
    return extract_airlock_client_from_request(data, user_api_key_dict)


def _is_client_pinned(original_model: str, data: dict[str, Any]) -> bool:
    """A request is pinned when the client sent a concrete model name."""
    if not original_model or original_model == "smart":
        return False
    metadata = data.get("metadata") or {}
    airlock_meta = metadata.get("airlock") or {}
    if airlock_meta.get("cost_tier") or airlock_meta.get("prefer_provider"):
        return False
    return True


def _set_model_override(
    data: dict[str, Any], requested_model: str, final_model: str, reason: str
) -> None:
    """Record an unpinned model override in metadata for logging/proxy surfaces."""
    metadata = data.setdefault("metadata", {})
    metadata["airlock_model_override"] = {
        "requested_model": requested_model,
        "final_model": final_model,
        "reason": reason,
    }
    metadata["airlock_response_headers"] = {
        "X-Airlock-Model-Override": final_model,
    }


def _raise_provider_protection(
    data: dict[str, Any],
    client_id: str,
    provider: str,
    model_name: str,
    reason: str,
    cooldown_seconds: float,
    scope: str = "provider",
) -> None:
    # Sanitize upstream reason text before embedding it anywhere client-visible.
    reason = sanitize_reason(reason)
    message = (
        f"Airlock temporarily blocked client {client_id} from provider {provider} "
        f"for model {model_name} to protect upstream standing. "
        f"Retry after {int(max(1, cooldown_seconds))} seconds. reason={reason}"
    )
    write_precall_block_record(
        data,
        error=message,
        error_type="RateLimitError",
        failure_category="provider",
    )
    # Typed subclass (workstream B): a FastAPI handler shapes this into a 429 with
    # Retry-After + X-Airlock-* headers. Still a RateLimitError for existing paths.
    raise AirlockProviderBlocked(
        message=message,
        llm_provider=provider,
        model=model_name,
        cooldown_seconds=cooldown_seconds,
        scope=scope,
        reason=reason,
        client_id=client_id,
    )


_DEFAULT_FALLBACK_MAX_PROMPT_TOKENS = 60000


def _fallback_max_prompt_tokens() -> int:
    raw = os.getenv("AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_FALLBACK_MAX_PROMPT_TOKENS


def _estimate_prompt_tokens(data: dict[str, Any]) -> int:
    """Cheap char/4 token estimate over message content (no tokenizer call)."""
    chars = 0
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chars += len(part["text"])
    return chars // 4


def _suppress_fallbacks(data: dict[str, Any], reason: str) -> None:
    """Disable downstream fan-out for this request (A2). Mirrors the pinned lock."""
    data["disable_fallbacks"] = True
    data["num_retries"] = 0
    data["max_retries"] = 0
    metadata = data.setdefault("metadata", {})
    metadata["airlock_fallback_suppressed"] = reason
    record_mutation(
        metadata,
        field="fallbacks",
        op="suppress",
        before=None,
        after=None,
        stage="pre_call",
        source="guardian.suppress",
        reason=reason,
    )


def _maybe_suppress_fallbacks(data: dict[str, Any]) -> None:
    """A2-1: suppress fallbacks for large-context requests (cost × payload).

    Scope note: this covers only the unambiguous large-prompt case. The
    rate-limited-provider case (A2-2) is handled by the circuit breaker's own
    failover (which redirects to a healthy model) plus same-provider fallback
    curation in config.yaml — suppressing here would fight that redirect.
    """
    if _estimate_prompt_tokens(data) > _fallback_max_prompt_tokens():
        _suppress_fallbacks(data, "large_prompt")


def _lock_pinned_request(data: dict[str, Any]) -> None:
    """Prevent downstream LiteLLM retries/fallbacks for pinned requests."""
    data["disable_fallbacks"] = True
    data["num_retries"] = 0
    data["max_retries"] = 0
    metadata = data.setdefault("metadata", {})
    metadata["airlock_pinned_request"] = {
        "disable_fallbacks": True,
        "num_retries": 0,
        "max_retries": 0,
    }
    record_mutation(
        metadata,
        field="fallbacks",
        op="suppress",
        before=None,
        after=None,
        stage="pre_call",
        source="guardian.pin",
        reason="pinned_request",
    )


# ---------------------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------------------
class AirlockFastGuardian(CustomGuardrail):
    """Pre-call guardrail implementing the fast reactive subsystem."""

    def __init__(self, **kwargs):
        supported_event_hooks = [
            GuardrailEventHooks.pre_call,
            GuardrailEventHooks.pre_mcp_call,
        ]
        super().__init__(supported_event_hooks=supported_event_hooks, **kwargs)

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> dict:
        now = time.time()
        client_id = _request_client_id(data, user_api_key_dict)
        # Resolve per-request guardrail skips (CC-10/CC-11) and stamp the decision
        # so content guards can honour it. Off by default; binds to the
        # authenticated key id, not the forgeable client header.
        from airlock.guardrail_overrides import resolve_guardrail_decision

        resolve_guardrail_decision(data, user_api_key_dict)
        client = store.get_client(client_id)
        requested_model = data.get("model") or "unknown"
        model_name = requested_model
        mcp = is_mcp_call(data, call_type)
        batch = is_batch_call(data, call_type)
        pinned_model = _is_client_pinned(requested_model, data)
        if pinned_model and not mcp and not batch:
            _lock_pinned_request(data)
        elif not mcp and not batch:
            # A2-1: large-context requests must not fan out across models.
            _maybe_suppress_fallbacks(data)

        # Record the inbound request
        client.record_request(now)

        # ---- Step 1: Backoff check (from a previous threat block) ----
        if client.is_in_backoff():
            remaining = client.backoff_until - now
            logger.warning(
                "client_in_backoff client=%s remaining=%.0fs",
                client_id,
                remaining,
            )
            raise ValueError(
                f"Too many requests. Please retry after {int(remaining)} seconds."
            )

        # ---- Step 2: Threat assessment ----
        message_text = extract_text(data, call_type) or None
        threat = assess_threat(client, message_text)
        if threat.blocked:
            raise ValueError(
                "Request blocked due to unusual activity. "
                f"Please retry after {int(threat.backoff_seconds)} seconds."
            )

        # ---- Step 2.5: Admission gate (C1 — off-by-default) ----
        if _admission_mod._admission_gate is not None:
            _priority_for_gate = compute_priority(client)
            try:
                admitted, retry_after = _admission_mod._admission_gate.check(
                    client_id, boost=_priority_for_gate.boost, now=now
                )
            except Exception:
                logger.warning(
                    "admission gate check raised — failing open", exc_info=True
                )
                admitted, retry_after = True, 0.0
            if not admitted:
                logger.warning(
                    "admission_shed client=%s retry_after=%.1fs", client_id, retry_after
                )
                data.setdefault("metadata", {})["airlock_admission"] = {
                    "action": "shed",
                    "retry_after": round(retry_after, 1),
                }
                raise ValueError(
                    f"Too many requests. Please retry after {int(retry_after) + 1} seconds."
                )
            data.setdefault("metadata", {})["airlock_admission"] = {
                "action": "admitted"
            }

        # Routing and circuit breaker are model-specific — skip for MCP and
        # batch/file calls (the latter carry no top-level model).
        if not mcp and not batch:
            # ---- Step 2.5a: Model alias resolution ----
            resolved = alias_table.resolve(model_name)
            if resolved and resolved != model_name:
                logger.info(
                    "model_alias %s -> %s",
                    model_name,
                    resolved,
                )
                data["model"] = resolved
                metadata = data.setdefault("metadata", {})
                metadata["airlock_alias"] = {
                    "original": model_name,
                    "resolved": resolved,
                }
                record_mutation(
                    metadata,
                    field="model",
                    op="rewrite",
                    before=model_name,
                    after=resolved,
                    stage="pre_call",
                    source="guardian.alias",
                )
                model_name = resolved

            # ---- Step 2.5b: Provider protection / intelligent routing ----
            if pinned_model:
                provider = infer_provider(model_name)
                if provider:
                    client_provider = store.get_client_provider(client_id, provider)
                    provider_state = store.get_provider(provider)
                    if client_provider.is_quarantined(now):
                        cooldown = client_provider.cooldown_remaining(now)
                        metadata = data.setdefault("metadata", {})
                        metadata["airlock_provider_protection"] = {
                            "action": "blocked_429",
                            "scope": "client_provider",
                            "client_id": client_id,
                            "provider": provider,
                            "requested_model": model_name,
                            "final_model": model_name,
                            "reason": client_provider.last_reason
                            or "provider_rate_limited",
                            "cooldown_seconds": round(cooldown, 1),
                        }
                        logger.warning(
                            "provider_protection action=blocked_429 scope=client_provider client=%s provider=%s model=%s cooldown=%.0fs reason=%s",
                            client_id,
                            provider,
                            model_name,
                            cooldown,
                            client_provider.last_reason or "provider_rate_limited",
                        )
                        _raise_provider_protection(
                            data,
                            client_id,
                            provider,
                            model_name,
                            client_provider.last_reason or "provider_rate_limited",
                            cooldown,
                            scope="client_provider",
                        )
                    if provider_state.is_quarantined(now):
                        cooldown = provider_state.cooldown_remaining(now)
                        metadata = data.setdefault("metadata", {})
                        metadata["airlock_provider_protection"] = {
                            "action": "blocked_429",
                            "scope": "provider",
                            "client_id": client_id,
                            "provider": provider,
                            "requested_model": model_name,
                            "final_model": model_name,
                            "reason": provider_state.last_reason
                            or "provider_rate_limited",
                            "cooldown_seconds": round(cooldown, 1),
                        }
                        logger.warning(
                            "provider_protection action=blocked_429 scope=provider client=%s provider=%s model=%s cooldown=%.0fs reason=%s",
                            client_id,
                            provider,
                            model_name,
                            cooldown,
                            provider_state.last_reason or "provider_rate_limited",
                        )
                        _raise_provider_protection(
                            data,
                            client_id,
                            provider,
                            model_name,
                            provider_state.last_reason or "provider_rate_limited",
                            cooldown,
                            scope="provider",
                        )
            else:
                data = apply_routing(data)
                model_name = data.get("model", model_name)  # re-read after routing
                if model_name != requested_model:
                    routing_meta = data.get("metadata", {}).get("airlock_routing", {})
                    _set_model_override(
                        data,
                        requested_model,
                        model_name,
                        ", ".join(routing_meta.get("reasons", [])) or "routed",
                    )

            # ---- Step 3: Circuit breaker / failover ----
            blocked_providers: set[str] = set()
            if not pinned_model:
                for provider_name, provider_state in store.all_providers().items():
                    if provider_state.is_quarantined(now):
                        blocked_providers.add(provider_name)
                current_provider = infer_provider(model_name)
                if current_provider:
                    client_provider = store.get_client_provider(
                        client_id, current_provider
                    )
                    if client_provider.is_quarantined(now):
                        blocked_providers.add(current_provider)

            failover = check_model_with_filters(
                model_name,
                blocked_providers=blocked_providers,
            )
            if not failover.allowed:
                if pinned_model:
                    provider = infer_provider(model_name) or "unknown"
                    logger.warning(
                        "provider_protection action=blocked_429 scope=model client=%s provider=%s model=%s reason=%s",
                        client_id,
                        provider,
                        model_name,
                        failover.reason,
                    )
                    _raise_provider_protection(
                        data,
                        client_id,
                        provider,
                        model_name,
                        failover.reason,
                        store.get_provider(provider).cooldown_remaining(now) or 30.0,
                        scope="model",
                    )
                elif failover.failover_model:
                    logger.info(
                        "model_failover original=%s failover=%s reason=%s",
                        model_name,
                        failover.failover_model,
                        failover.reason,
                    )
                    data["model"] = failover.failover_model
                    metadata = data.setdefault("metadata", {})
                    metadata["airlock_failover"] = {
                        "original_model": model_name,
                        "failover_model": failover.failover_model,
                        "reason": failover.reason,
                    }
                    record_mutation(
                        metadata,
                        field="model",
                        op="rewrite",
                        before=model_name,
                        after=failover.failover_model,
                        stage="pre_call",
                        source="guardian.failover",
                        reason=failover.reason,
                    )
                    _set_model_override(
                        data,
                        requested_model,
                        failover.failover_model,
                        failover.reason,
                    )
                else:
                    raise ValueError(
                        f"Model {model_name} is currently unavailable and no "
                        f"fallback models are healthy. Please try again shortly."
                    )

        # ---- Step 4: Priority scoring ----
        target_provider = infer_provider(data.get("model") or model_name)
        data = apply_gemini_request_semantics(data, provider=target_provider)
        # Translate an off-intent / provider-invalid reasoning_effort (e.g. "none"
        # for OpenAI) to the target provider's floor BEFORE litellm's drop_params
        # silently strips it and the model falls back to its default reasoning.
        # client_id is passed so the warn-only `effort_would_reject` event can be
        # attributed to a specific caller (design §13.2) rather than a bare total.
        normalize_reasoning_effort(data, target_provider, client_id=client_id)
        # Derived drop_params transparency (Decision 8): record each client param the
        # resolved provider does not support as an op="drop" — once per request.
        if target_provider:
            ledger_metadata = data.setdefault("metadata", {})
            for dropped_param in detect_dropped_params(
                data, data.get("model") or model_name, target_provider
            ):
                record_mutation(
                    ledger_metadata,
                    field=dropped_param,
                    op="drop",
                    before=None,
                    after=None,
                    stage="pre_call",
                    source="drop_params",
                    reason="provider-unsupported (drop_params)",
                )
        priority = compute_priority(client)
        metadata = data.setdefault("metadata", {})
        metadata["airlock_priority"] = {
            "score": round(priority.score, 3),
            "boost": priority.boost,
            "reasons": priority.reasons,
        }
        metadata["airlock_request"] = {
            "client_id": client_id,
            "requested_model": requested_model,
            "final_model": data.get("model", model_name),
            "pinned_model": pinned_model,
            "provider": infer_provider(data.get("model", model_name)),
        }
        if priority.boost:
            logger.info(
                "priority_boost client=%s score=%.3f reasons=%s",
                client_id,
                priority.score,
                priority.reasons,
            )

        return data
