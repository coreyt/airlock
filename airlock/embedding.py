"""Embedding-request validation owned by Airlock's pre-dispatch boundary."""

from __future__ import annotations

from typing import Any

from litellm.proxy._types import ProxyException


class AirlockInvalidEmbeddingOption(ProxyException):
    """A client supplied an embedding option outside Airlock's contract."""

    def __init__(self, option: str, reason: str) -> None:
        self.option = option
        self._airlock_error_code = "invalid_embedding_option"
        super().__init__(
            message=f"Embedding option {option!r} is invalid: {reason}.",
            type="invalid_request_error",
            param=option,
            code=400,
            openai_code=self._airlock_error_code,
        )

    def to_dict(self) -> dict:
        body = super().to_dict()
        body["code"] = self._airlock_error_code
        return body


# This is intentionally an allowlist, rather than a list of known bad chat
# controls.  Airlock uses LiteLLM with ``drop_params: true`` globally, so an
# unknown client body member must not reach transport where it could be silently
# removed. Internal fields are added by the LiteLLM proxy before pre-call hooks.
_CLIENT_EMBEDDING_FIELDS = frozenset(
    {"model", "input", "dimensions", "encoding_format", "user"}
)
_PROXY_INTERNAL_EMBEDDING_FIELDS = frozenset(
    {
        "metadata",
        "secret_fields",
        "litellm_call_id",
        "litellm_session_id",
        "litellm_trace_id",
        "litellm_logging_obj",
        "proxy_server_request",
        "api_version",
        "request_timeout",
        "timeout",
        "stream_timeout",
        "max_retries",
        "num_retries",
        "disable_fallbacks",
        "model_info",
        "litellm_params",
        "caching",
        "cache",
        "ttl",
        "organization",
        "allowed_model_region",
    }
)


def validate_embedding_options(data: dict[str, Any]) -> None:
    """Reject invalid or completion-only embedding options before dispatch.

    The public contract is ``model``, ``input``, optional ``user``, optional
    positive ``dimensions`` up to the model's native 1536 dimensions, and
    OpenAI's ``float``/``base64`` encoding formats. The permit list excludes
    proxy-created internal fields, which are not client API options. Airlock
    intentionally rejects direct-dispatch and header controls (`api_base`,
    `api_key`, provider/deployment controls, and `headers`) even if they appear
    after proxy processing: LiteLLM's embedding route can honor client-supplied
    versions and would bypass the reviewed model-list deployment.
    """
    for option in data:
        if option not in _CLIENT_EMBEDDING_FIELDS | _PROXY_INTERNAL_EMBEDDING_FIELDS:
            raise AirlockInvalidEmbeddingOption(option, "not supported for embeddings")

    if "dimensions" in data:
        dimensions = data["dimensions"]
        if (
            isinstance(dimensions, bool)
            or not isinstance(dimensions, int)
            or not 1 <= dimensions <= 1536
        ):
            raise AirlockInvalidEmbeddingOption(
                "dimensions", "must be an integer from 1 through 1536"
            )

    if "encoding_format" in data and data["encoding_format"] not in {
        "float",
        "base64",
    }:
        raise AirlockInvalidEmbeddingOption(
            "encoding_format", "must be 'float' or 'base64'"
        )


def validate_embedding_client_body(data: object) -> None:
    """Validate raw `/v1/embeddings` JSON before LiteLLM augments it.

    This is deliberately separate from :func:`validate_embedding_options`.
    Once LiteLLM has mixed its own fields into the request, the guardrail cannot
    establish whether an `api_base` or `headers` value originated in the client
    body. The authenticated route wrapper calls this function before that mixing
    occurs.
    """
    if not isinstance(data, dict):
        raise AirlockInvalidEmbeddingOption("body", "must be a JSON object")
    for option in data:
        if option not in _CLIENT_EMBEDDING_FIELDS:
            raise AirlockInvalidEmbeddingOption(option, "not supported for embeddings")
    validate_embedding_options(data)
