"""Emit Airlock response headers at the LiteLLM proxy boundary."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from airlock.litellm_adapter import merge_additional_headers
from airlock.proxy_bootstrap import bootstrap_airlock_proxy
from airlock.gemini_interface import (
    build_gemini_response_headers,
    classify_gemini_response,
    is_gemini_provider,
)
from airlock.transparency import (
    attribute_served_backend,
    get_transparency_config,
    mutations_header,
    served_headers,
)


class AirlockModelOverrideHeaders(CustomLogger):
    """Expose Airlock-selected response headers on outbound HTTP responses."""

    async def async_post_call_response_headers_hook(
        self,
        data: dict,
        user_api_key_dict: Any,
        response: Any,
        request_headers: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        metadata = (data or {}).get("metadata") or {}
        response_headers = dict(metadata.get("airlock_response_headers") or {})
        if not response_headers and is_gemini_provider(
            (data or {}).get("model"),
            (metadata.get("airlock_request") or {}).get("provider"),
        ):
            request_meta = metadata.get("airlock_gemini") or {
                "mode": "balanced",
            }
            response_meta = classify_gemini_response(response) or {}
            if response_meta:
                response_headers = build_gemini_response_headers(
                    request_meta,
                    response_meta,
                )

        # Served-backend attribution (OBS-served). Read the backend that actually
        # served the request from the response (never guessed from the model name);
        # no cost_fallback — response_cost is finalized later in the log hook.
        served = attribute_served_backend(response)
        if (
            served is not None
            and served.provider is not None
            and isinstance(data, dict)
        ):
            served_meta = data.get("metadata")
            if not isinstance(served_meta, dict):
                served_meta = {}
                data["metadata"] = served_meta
            served_meta["airlock_served"] = {
                "provider": served.provider,
                "api_base_host": served.api_base_host,
                "region": served.region,
                "model_id": served.model_id,
                "backend_kind": served.backend_kind,
            }
        cfg = get_transparency_config()
        if cfg.served_headers:
            response_headers.update(served_headers(served))

        # X-Airlock-Mutations (OBS-headers Part A): serialize the pre/during-call
        # ledger via the allowlist-aware, byte-bounded serializer (CC-T2). Additive
        # (CC-T7); fires for streaming + non-streaming alike (CC-T6).
        ledger = metadata.get("airlock_mutations") or []
        if ledger and cfg.mutation_headers != "off":
            header_val = mutations_header(ledger, cfg.mutation_header_budget_bytes)
            if header_val:
                response_headers["X-Airlock-Mutations"] = header_val

        if not response_headers:
            return None

        merge_additional_headers(response, response_headers)

        return dict(response_headers)

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: Any,  # noqa: ARG002
        response: Any,
    ) -> Any:
        """Attach the additive ``airlock.mutations`` body envelope (OBS-headers
        Part B), NON-STREAMING ONLY (streaming uses the iterator hook, which is
        intentionally not implemented — Decision 7). The default path (no opt-in)
        is a byte-identical no-op (CC-T5/CC-T7)."""
        cfg = get_transparency_config()
        if not self._explain_opted_in(data, cfg.explain_body_optin_header):
            return response

        metadata = (data or {}).get("metadata") or {}
        ledger = metadata.get("airlock_mutations") or []
        if not ledger or not hasattr(response, "model_dump"):
            return response

        # Metadata-only, value-safe (CC-T2); ModelResponse has extra="allow" so a
        # plain attribute serializes into the client-visible JSON.
        response.airlock = {"mutations": [asdict(m) for m in ledger]}
        return response

    @staticmethod
    def _explain_opted_in(data: dict, header_name: str) -> bool:
        headers = ((data or {}).get("proxy_server_request") or {}).get("headers") or {}
        if not isinstance(headers, dict):
            return False
        target = header_name.lower()
        for key, value in headers.items():
            if isinstance(key, str) and key.lower() == target:
                return _coerce_optin(value)
        return False


_FALSY_OPTIN = {"", "0", "false", "off", "no"}


def _coerce_optin(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY_OPTIN
    return bool(value)


proxy_model_override_headers = AirlockModelOverrideHeaders()

# Airlock runs on top of LiteLLM's FastAPI app. LiteLLM loads this callback module
# (config.yaml), so trigger the proxy seam installs once here at import time. The
# install order is owned by airlock.proxy_bootstrap.
bootstrap_airlock_proxy()
