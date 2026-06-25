import logging
from typing import Any

from litellm.integrations.custom_guardrail import CustomGuardrail

from airlock.providers.enhanced_passthrough import enhanced_handler
from airlock.transparency import record_mutation


class EnhancedModelInterceptor(CustomGuardrail):
    """Middleware for intercepting and mutating enhanced model requests."""

    async def async_pre_call(
        self, data: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        """Intercepts the request, mutates the payload, and rewrites the model target."""
        litellm_params = data.get("litellm_params", {})
        enhanced_profile = litellm_params.get("enhanced_profile")

        if not enhanced_profile:
            self._record_config_fallback_injection(data, litellm_params)
            return data

        target_model = enhanced_profile.get("target_model")
        system_prompt = enhanced_profile.get("system_prompt")
        params_override = enhanced_profile.get("params", {})

        if not target_model:
            logging.warning(
                f"Enhanced profile for {data.get('model')} is missing 'target_model'"
            )
            return data

        if system_prompt:
            messages = data.get("messages") or []
            self._inject_or_append_system_prompt(messages, system_prompt)
            data["messages"] = messages
            record_mutation(
                data.setdefault("metadata", {}),
                field="system",
                op="inject",
                before=None,
                after=None,
                stage="pre_call",
                source="enhanced.interceptor",
                reason=enhanced_profile.get("name") or data.get("model"),
            )

        if params_override:
            optional_params = data.get("optional_params") or {}
            optional_params.update(params_override)
            data["optional_params"] = optional_params

        data["model"] = target_model

        return data

    def _record_config_fallback_injection(
        self, data: dict[str, Any], litellm_params: dict[str, Any]
    ) -> None:
        """Record the config-resolved system-prompt injection the passthrough
        provider performs at execution time.

        Site 11 (above) only fires when ``litellm_params`` carries the
        ``enhanced_profile``. A model whose profile is resolved from the config
        cache injects a ``system_prompt`` inside the provider, which the ledger
        never sees. Here — the pre-call point that holds the OUTER metadata — we
        reuse the SAME resolution the provider uses and append exactly one
        value-free ``inject`` record. Observe-only: no payload mutation.
        """
        model = data.get("model")
        if not model:
            return
        profile = enhanced_handler._resolve_profile(model, litellm_params)
        if not profile.get("target_model") or not profile.get("system_prompt"):
            return
        record_mutation(
            data.setdefault("metadata", {}),
            field="system",
            op="inject",
            before=None,
            after=None,
            stage="pre_call",
            source="enhanced.passthrough",
            reason=profile.get("name") or model,
        )

    def _inject_or_append_system_prompt(
        self, messages: list[dict[str, Any]], system_prompt: str
    ) -> None:
        """Inject a system prompt at the beginning or append to an existing one."""
        if not messages:
            messages.append({"role": "system", "content": system_prompt})
            return

        first_msg = messages[0]
        if first_msg.get("role") == "system":
            original_content = first_msg.get("content", "")
            if original_content:
                first_msg["content"] = f"{original_content}\n\n{system_prompt}"
            else:
                first_msg["content"] = system_prompt
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})
