"""
Airlock Enforcer — pre_call guardrail with adaptive weighted blocking.

Runs 4th in the pre_call chain (after PII, keyword, fast guardian).
Reads analyzer-tuned knobs and blocks requests whose composite score
exceeds the threshold.

Enforcement is a config toggle via AIRLOCK_ENFORCE_MODE env var:
  - observe  (default) — no-op, returns data immediately
  - shadow   — evaluates, logs what it would block, never raises
  - enforce  — evaluates, blocks above threshold
"""

from __future__ import annotations

import logging
import os
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.guardrail_overrides import _authenticated_client_id
from airlock.guardrails.observer import collect_signals
from airlock.guardrails.orchestrator import _get_knobs, evaluate
from airlock.paid_services import check_or_raise

logger = logging.getLogger("airlock.guardrails.enforcer")

_VALID_MODES = {"observe", "shadow", "enforce"}


def _enforce_mode() -> str:
    """Read AIRLOCK_ENFORCE_MODE, defaulting to 'observe'."""
    mode = os.getenv("AIRLOCK_ENFORCE_MODE", "observe").lower().strip()
    if mode not in _VALID_MODES:
        logger.warning("invalid_enforce_mode mode=%r — defaulting to observe", mode)
        return "observe"
    return mode


def _authorize_paid_service(data: dict, user_api_key_dict: Any) -> None:
    """Gate paid side services on the authenticated client identity (#21).

    Reuses the existing seams rather than adding an authorization layer: the
    identity comes from ``_authenticated_client_id`` (key-derived, not the
    forgeable ``X-Airlock-Client`` header) and the enforcement point is this
    guardrail, which LiteLLM already invokes on every request.

    Raises ``PermissionError`` when refused; records the decision either way so
    an operator can see which requests the policy touched.
    """
    model = data.get("model") or data.get("mcp_tool_name")
    client_id = _authenticated_client_id(user_api_key_dict)
    decision = check_or_raise(model, client_id)
    if decision.service is not None:
        metadata = data.setdefault("metadata", {})
        metadata["airlock_paid_service"] = decision.as_metadata()


class AirlockEnforcer(CustomGuardrail):
    """Pre-call guardrail with adaptive weighted blocking."""

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
        # Authorization runs before the enforcement-mode check on purpose.
        # `observe` means "score but do not block on heuristics"; it does not
        # mean "let any caller spend another tenant's paid credits". An authz
        # decision is a hard gate, not a weighted signal, so it is never
        # subject to the adaptive threshold.
        _authorize_paid_service(data, user_api_key_dict)

        mode = _enforce_mode()
        if mode == "observe":
            return data

        signals = collect_signals(data, user_api_key_dict, call_type)
        knobs = _get_knobs()
        composite_score = evaluate(signals, knobs)
        should_block = composite_score >= knobs.threshold

        metadata = data.setdefault("metadata", {})
        metadata["airlock_enforcement"] = {
            "mode": mode,
            "composite_score": round(composite_score, 4),
            "threshold": knobs.threshold,
            "should_block": should_block,
        }

        if should_block:
            logger.warning(
                "enforcement mode=%s score=%.4f threshold=%.4f should_block=%s",
                mode,
                composite_score,
                knobs.threshold,
                should_block,
            )

        if should_block and mode == "enforce":
            raise ValueError("Request blocked by Airlock adaptive guardrails.")

        return data
