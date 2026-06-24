"""Emit Airlock response headers at the LiteLLM proxy boundary."""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from airlock.admin.http import install_admin_on_proxy_app
from airlock.batch.middleware import install_batch_gateway_on_proxy_app
from airlock.docs import install_airlock_docs_on_proxy_app
from airlock.health import install_circuit_health_on_proxy_app
from airlock.proxy_errors import install_airlock_error_handlers_on_proxy_app
from airlock.gemini_interface import (
    build_gemini_response_headers,
    classify_gemini_response,
    is_gemini_provider,
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
        response_headers = metadata.get("airlock_response_headers") or {}
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
        if not response_headers:
            return None

        hidden_params = getattr(response, "_hidden_params", None)
        if isinstance(hidden_params, dict):
            additional_headers = hidden_params.setdefault("additional_headers", {})
            additional_headers.update(response_headers)

        return dict(response_headers)


proxy_model_override_headers = AirlockModelOverrideHeaders()

# Airlock runs on top of LiteLLM's FastAPI app, so enrich the existing docs in place.
install_airlock_docs_on_proxy_app()
install_circuit_health_on_proxy_app()
install_airlock_error_handlers_on_proxy_app()
# Admin perimeter mounts BEFORE the batch gateway so the gateway stays the
# outermost ASGI layer (umbrella §3 mount order).
install_admin_on_proxy_app()
install_batch_gateway_on_proxy_app()
