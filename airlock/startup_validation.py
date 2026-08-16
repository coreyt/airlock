"""Pure, redacted startup validation for configured provider credentials."""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

import yaml

from airlock.capability import airlock_provider_for


@dataclass(frozen=True)
class ProviderCredentialSpec:
    provider: str
    environment_variable: str


@dataclass(frozen=True)
class ProviderCredentialWarning:
    provider: str
    credential_configured: bool = True
    configured_alias_count: int = 0
    source: str = "startup_validation"


PROVIDER_CREDENTIAL_SPECS = (
    ProviderCredentialSpec("anthropic", "ANTHROPIC_API_KEY"),
    ProviderCredentialSpec("openai", "OPENAI_API_KEY"),
    ProviderCredentialSpec("gemini", "GOOGLE_AISTUDIO_API_KEY"),
    ProviderCredentialSpec("mistral", "MISTRAL_API_KEY"),
    ProviderCredentialSpec("openrouter", "OPENROUTER_API_KEY"),
    ProviderCredentialSpec("deepseek", "DEEPSEEK_API_KEY"),
    ProviderCredentialSpec("perplexity", "PERPLEXITY_API_KEY"),
    ProviderCredentialSpec("tavily", "TAVILY_API_KEY"),
    ProviderCredentialSpec("vllm", "VLLM_API_KEY"),
)


def _load_mapping(path: Path) -> dict:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"could not load startup validation config: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("startup validation config must contain a mapping")
    return loaded


def effective_model_list(config_path: str | Path) -> list[dict]:
    """Return model entries using LiteLLM's direct, non-recursive includes."""
    path = Path(config_path).resolve()
    config = copy.deepcopy(_load_mapping(path))
    includes = config.pop("include", None)
    if includes is not None:
        if not isinstance(includes, list) or not all(
            isinstance(item, str) for item in includes
        ):
            raise ValueError("config include must be a list of paths")
        for item in includes:
            include_path = Path(item)
            if not include_path.is_absolute():
                include_path = path.parent / include_path
            included = _load_mapping(include_path.resolve())
            for key, value in included.items():
                if isinstance(value, list) and isinstance(config.get(key), list):
                    config[key].extend(value)
                else:
                    config[key] = value
    entries = config.get("model_list") or []
    return [entry for entry in entries if isinstance(entry, dict)]


def _enabled_provider_counts(entries: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        params = entry.get("litellm_params") or {}
        provider = airlock_provider_for(entry)
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
        if (
            params.get("backend") == "vllm"
            and params.get("api_key") == "os.environ/VLLM_API_KEY"
        ):
            counts["vllm"] = counts.get("vllm", 0) + 1
    return counts


def credential_without_alias_warnings(
    config_path: str | Path, getenv: Callable[[str], str | None]
) -> tuple[ProviderCredentialWarning, ...]:
    """Return one safe warning for each recognised present-but-unused credential."""
    enabled = _enabled_provider_counts(effective_model_list(config_path))
    return tuple(
        ProviderCredentialWarning(spec.provider)
        for spec in PROVIDER_CREDENTIAL_SPECS
        if (value := getenv(spec.environment_variable)) is not None
        and value.strip()
        and enabled.get(spec.provider, 0) == 0
    )


def emit_provider_credential_warnings(
    warnings: tuple[ProviderCredentialWarning, ...], *, stream: TextIO = sys.stderr
) -> None:
    """Emit the fixed-schema, secret-free startup warning to stderr."""
    for warning in warnings:
        print(
            "WARNING: airlock.startup.provider_credential_without_alias "
            f"provider={warning.provider} credential_configured=true "
            "configured_alias_count=0 source=startup_validation",
            file=stream,
        )
