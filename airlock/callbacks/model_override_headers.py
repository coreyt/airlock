"""Emit Airlock model-override response headers at the LiteLLM proxy boundary."""

from __future__ import annotations

from typing import Any

from litellm.integrations.custom_logger import CustomLogger


class AirlockModelOverrideHeaders(CustomLogger):
    """Expose Airlock-selected model overrides on outbound HTTP responses."""

    async def async_post_call_response_headers_hook(
        self,
        data: dict,
        user_api_key_dict: Any,
        response: Any,
        request_headers: dict[str, str] | None = None,
    ) -> dict[str, str] | None:
        metadata = (data or {}).get("metadata") or {}
        response_headers = metadata.get("airlock_response_headers") or {}
        override = response_headers.get("X-Airlock-Model-Override")
        if not override:
            return None

        hidden_params = getattr(response, "_hidden_params", None)
        if isinstance(hidden_params, dict):
            additional_headers = hidden_params.setdefault("additional_headers", {})
            additional_headers["X-Airlock-Model-Override"] = override

        return {"X-Airlock-Model-Override": override}


proxy_model_override_headers = AirlockModelOverrideHeaders()
