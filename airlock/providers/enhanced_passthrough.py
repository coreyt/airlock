"""Custom provider stub for enhanced/* logical model aliases.

LiteLLM validates provider prefixes while building router deployments. Airlock's
``enhanced/*`` aliases are logical wrappers that are rewritten by the
EnhancedModelInterceptor before execution, so they need a provider registration
for startup but should never execute directly.
"""

from __future__ import annotations

from typing import Callable, Optional, Union

import httpx
from litellm.llms.custom_llm import CustomLLM
from litellm.types.utils import ModelResponse


class EnhancedPassthroughProvider(CustomLLM):
    """Fail fast if an enhanced alias reaches execution without rewrite."""

    def _error(self, model: str) -> ValueError:
        return ValueError(
            f"Logical model '{model}' reached provider execution before Airlock "
            "rewrote it to a physical target model."
        )

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
        raise self._error(model)

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
        raise self._error(model)


enhanced_handler = EnhancedPassthroughProvider()
