"""Custom provider for enhanced/* logical model aliases.

LiteLLM validates provider prefixes while building router deployments. Airlock's
``enhanced/*`` aliases are logical wrappers around a physical target model plus
request mutations (system prompt injection, param overrides). Guardrails may
rewrite these requests earlier, but the provider itself must also be able to
honor the alias so execution does not depend on guardrail ordering.
"""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
from typing import Any, Optional, Union

import httpx
import litellm
import yaml
from litellm.llms.custom_llm import CustomLLM
from litellm.types.utils import ModelResponse


def _normalize_enhanced_params(
    target_model: str,
    params_override: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(params_override)

    if "gemini" not in target_model:
        return normalized

    thinking_level = normalized.pop("thinking_level", None)
    thinking = normalized.pop("thinking", None)

    if thinking is False:
        normalized.setdefault("reasoning_effort", "disable")
        return normalized

    if thinking:
        if thinking_level is not None:
            normalized.setdefault("reasoning_effort", str(thinking_level).strip().lower())
        else:
            normalized.setdefault("reasoning_effort", "medium")

    return normalized


def _inject_or_append_system_prompt(
    messages: list[dict[str, Any]], system_prompt: str
) -> list[dict[str, Any]]:
    if not messages:
        return [{"role": "system", "content": system_prompt}]

    copied = [dict(message) for message in messages]
    first = copied[0]
    if first.get("role") == "system":
        original_content = first.get("content", "")
        first["content"] = (
            f"{original_content}\n\n{system_prompt}" if original_content else system_prompt
        )
        return copied

    return [{"role": "system", "content": system_prompt}, *copied]


class EnhancedPassthroughProvider(CustomLLM):
    """Resolve enhanced aliases to their physical target model on execution."""

    def __init__(self) -> None:
        super().__init__()
        self._config_profile_cache: dict[str, dict[str, Any]] | None = None
        self._config_profile_cache_key: str | None = None

    def _config_path(self) -> Path:
        configured = os.getenv("AIRLOCK_CONFIG")
        if configured:
            return Path(configured)
        return Path(__file__).resolve().parents[2] / "config.yaml"

    def _load_profile_cache(self) -> dict[str, dict[str, Any]]:
        config_path = self._config_path()
        cache_key = str(config_path.resolve()) if config_path.exists() else str(config_path)
        if self._config_profile_cache is not None and self._config_profile_cache_key == cache_key:
            return self._config_profile_cache

        cache: dict[str, dict[str, Any]] = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
            for entry in config.get("model_list") or []:
                model_name = entry.get("model_name")
                litellm_params = entry.get("litellm_params") or {}
                enhanced_profile = litellm_params.get("enhanced_profile")
                if model_name and enhanced_profile:
                    cache[str(model_name)] = dict(enhanced_profile)
                provider_model = litellm_params.get("model")
                if isinstance(provider_model, str) and provider_model.startswith("enhanced/") and enhanced_profile:
                    cache[provider_model.split("/", 1)[1]] = dict(enhanced_profile)

        self._config_profile_cache = cache
        self._config_profile_cache_key = cache_key
        return cache

    def _resolve_profile(self, model: str, litellm_params: dict | None) -> dict[str, Any]:
        profile = dict((litellm_params or {}).get("enhanced_profile") or {})
        if profile:
            return profile
        return dict(self._load_profile_cache().get(model) or {})

    def _resolve_request(
        self,
        *,
        model: str,
        messages: list,
        optional_params: dict,
        litellm_params: dict | None,
        api_key: Any,
        api_base: str | None,
        headers: dict[str, Any] | None,
        client: Any,
    ) -> tuple[str, list, dict]:
        profile = self._resolve_profile(model, litellm_params)
        target_model = profile.get("target_model")
        if not target_model:
            raise ValueError(
                f"Logical model '{model}' reached provider execution without "
                "enhanced_profile.target_model."
            )

        resolved_messages = list(messages or [])
        system_prompt = profile.get("system_prompt")
        if system_prompt:
            resolved_messages = _inject_or_append_system_prompt(
                resolved_messages, system_prompt
            )

        resolved_params = dict(optional_params or {})
        params_override = profile.get("params") or {}
        if params_override:
            resolved_params.update(
                _normalize_enhanced_params(target_model, params_override)
            )

        # Inner provider call should execute the physical model only once.
        resolved_params.pop("custom_llm_provider", None)
        resolved_params["no_log"] = True
        metadata = dict(resolved_params.get("metadata") or {})
        metadata["airlock_skip_fathom_logger"] = True
        resolved_params["metadata"] = metadata
        if api_key is not None:
            resolved_params["api_key"] = api_key
        if api_base:
            resolved_params["api_base"] = api_base
        if headers:
            resolved_params["headers"] = headers
        if client is not None:
            resolved_params["client"] = client

        return target_model, resolved_messages, resolved_params

    def completion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding,
        api_key,
        logging_obj,
        optional_params: dict,
        acompletion=None,
        litellm_params=None,
        logger_fn=None,
        headers=None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client=None,
    ) -> ModelResponse:
        target_model, resolved_messages, resolved_params = self._resolve_request(
            model=model,
            messages=messages,
            optional_params=optional_params,
            litellm_params=litellm_params,
            api_key=api_key,
            api_base=api_base,
            headers=headers,
            client=client,
        )

        return litellm.completion(
            model=target_model,
            messages=resolved_messages,
            timeout=timeout,
            **resolved_params,
        )

    async def acompletion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable,
        encoding,
        api_key,
        logging_obj,
        optional_params: dict,
        acompletion=None,
        litellm_params=None,
        logger_fn=None,
        headers=None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        client=None,
    ) -> ModelResponse:
        target_model, resolved_messages, resolved_params = self._resolve_request(
            model=model,
            messages=messages,
            optional_params=optional_params,
            litellm_params=litellm_params,
            api_key=api_key,
            api_base=api_base,
            headers=headers,
            client=client,
        )

        return await litellm.acompletion(
            model=target_model,
            messages=resolved_messages,
            timeout=timeout,
            **resolved_params,
        )


enhanced_handler = EnhancedPassthroughProvider()
