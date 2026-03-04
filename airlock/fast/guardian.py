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
import time
from typing import Any

from litellm import DualCache
from litellm.integrations.custom_guardrail import CustomGuardrail
from litellm.types.guardrails import GuardrailEventHooks

from airlock.guardrails.extract import extract_text, is_mcp_call

from .circuit_breaker import check_model
from .priority import compute_priority
from .router import apply_routing
from .state import store
from .threat_detector import assess_threat

logger = logging.getLogger("airlock.fast.guardian")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_client_id(user_api_key_dict: Any) -> str:
    """Derive a stable client identifier from the API-key metadata."""
    if user_api_key_dict:
        if hasattr(user_api_key_dict, "api_key"):
            key = user_api_key_dict.api_key or ""
            if len(key) > 8:
                return f"key:{key[-8:]}"
        if isinstance(user_api_key_dict, dict):
            return f"key:{user_api_key_dict.get('api_key', 'unknown')[-8:]}"
    return "unknown"


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
        client_id = _extract_client_id(user_api_key_dict)
        client = store.get_client(client_id)
        model_name = data.get("model", "unknown")
        mcp = is_mcp_call(data, call_type)

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

        # Routing and circuit breaker are model-specific — skip for MCP calls
        if not mcp:
            # ---- Step 2.5: Intelligent routing ----
            data = apply_routing(data)
            model_name = data.get("model", model_name)  # re-read after routing

            # ---- Step 3: Circuit breaker / failover ----
            failover = check_model(model_name)
            if not failover.allowed:
                if failover.failover_model:
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
                else:
                    raise ValueError(
                        f"Model {model_name} is currently unavailable and no "
                        f"fallback models are healthy. Please try again shortly."
                    )

        # ---- Step 4: Priority scoring ----
        priority = compute_priority(client)
        metadata = data.setdefault("metadata", {})
        metadata["airlock_priority"] = {
            "score": round(priority.score, 3),
            "boost": priority.boost,
            "reasons": priority.reasons,
        }
        if priority.boost:
            logger.info(
                "priority_boost client=%s score=%.3f reasons=%s",
                client_id,
                priority.score,
                priority.reasons,
            )

        return data
