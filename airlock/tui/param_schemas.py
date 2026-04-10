"""Per-provider parameter schemas for the Chat TUI builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParamField:
    """One parameter field in the builder overlay."""

    name: str
    label: str
    type: str  # "float", "int", "str", "bool", "enum"
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: list[str] = field(default_factory=list)


@dataclass
class ProviderSchema:
    """Parameter schema for a provider."""

    fields: list[ParamField]


# ---------------------------------------------------------------------------
# Common field factories (avoid repetition across providers)
# ---------------------------------------------------------------------------


def _temperature(default: float = 1.0, max_val: float = 1.0) -> ParamField:
    return ParamField("temperature", "Temperature", "float", default, 0.0, max_val)


def _max_tokens(default: int = 1024, max_val: int = 128_000) -> ParamField:
    return ParamField("max_tokens", "Max Tokens", "int", default, 1, max_val)


def _top_p() -> ParamField:
    return ParamField("top_p", "Top P", "float", None, 0.0, 1.0)


def _top_k(max_val: int | None = None) -> ParamField:
    return ParamField("top_k", "Top K", "int", None, 0, max_val)


def _stop() -> ParamField:
    return ParamField("stop", "Stop (comma-sep)", "str", None)


def _system() -> ParamField:
    return ParamField("system", "System Prompt", "str", None)


# ---------------------------------------------------------------------------
# Provider schemas
# ---------------------------------------------------------------------------

PROVIDER_SCHEMAS: dict[str, ProviderSchema] = {
    "anthropic": ProviderSchema(
        fields=[
            _temperature(1.0, 1.0),
            _max_tokens(1024, 128_000),
            _top_p(),
            _top_k(),
            _stop(),
            _system(),
        ]
    ),
    "openai": ProviderSchema(
        fields=[
            _temperature(1.0, 2.0),
            _max_tokens(1024, 128_000),
            _top_p(),
            ParamField(
                "frequency_penalty", "Frequency Penalty", "float", 0.0, -2.0, 2.0
            ),
            ParamField("presence_penalty", "Presence Penalty", "float", 0.0, -2.0, 2.0),
            _stop(),
            _system(),
        ]
    ),
    "gemini": ProviderSchema(
        fields=[
            _temperature(1.0, 2.0),
            ParamField(
                "max_output_tokens", "Max Output Tokens", "int", 1024, 1, 65_536
            ),
            _top_p(),
            _top_k(),
            _stop(),
            _system(),
        ]
    ),
    "mistral": ProviderSchema(
        fields=[
            _temperature(0.7, 1.0),
            _max_tokens(1024, 128_000),
            _top_p(),
            _stop(),
            _system(),
        ]
    ),
    "perplexity": ProviderSchema(
        fields=[
            _temperature(0.2, 1.5),
            _max_tokens(1024, 128_000),
            _top_p(),
            ParamField("return_citations", "Return Citations", "bool", False),
            ParamField(
                "search_recency_filter",
                "Search Recency",
                "enum",
                None,
                choices=["month", "week", "day", "hour"],
            ),
            _system(),
        ]
    ),
    "tavily": ProviderSchema(
        fields=[
            ParamField("max_results", "Max Results", "int", 5, 1, 20),
            ParamField(
                "search_depth",
                "Search Depth",
                "enum",
                "basic",
                choices=["basic", "advanced"],
            ),
        ]
    ),
    "local": ProviderSchema(
        fields=[
            _temperature(0.7, 2.0),
            _max_tokens(1024, 32_768),
            _top_p(),
            _top_k(),
            ParamField(
                "repetition_penalty", "Repetition Penalty", "float", None, 0.0, 2.0
            ),
            _stop(),
        ]
    ),
}

# Fallback for unknown providers or the "all" meta-provider.
DEFAULT_SCHEMA = ProviderSchema(
    fields=[
        _temperature(0.7, 2.0),
        _max_tokens(1024),
        _top_p(),
        _top_k(),
        _stop(),
        _system(),
    ]
)

# Optional model-level overrides keyed by (provider, model_name).
MODEL_OVERRIDES: dict[tuple[str, str], ProviderSchema] = {}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_schema(provider: str, model_name: str | None = None) -> ProviderSchema:
    """Return the parameter schema for *provider* (with optional model override)."""
    if model_name and (provider, model_name) in MODEL_OVERRIDES:
        return MODEL_OVERRIDES[(provider, model_name)]
    return PROVIDER_SCHEMAS.get(provider, DEFAULT_SCHEMA)


def defaults_for_schema(schema: ProviderSchema) -> dict[str, Any]:
    """Return ``{field.name: field.default}`` for fields with non-None defaults."""
    return {f.name: f.default for f in schema.fields if f.default is not None}
