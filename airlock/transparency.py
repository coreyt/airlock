"""Mutation ledger + served-backend attribution (0.5.0 transparency workstream).

Pure, call-site-free module (OBS-core). Holds the canonical mutation-ledger and
served-backend dataclasses/helpers, the header serializers, and the
``transparency.*`` config loader. Later packs import these and wire them into the
request path; this module changes no behavior on its own. Realizes §3 of
``dev/notes/design-mutation-and-provider-transparency.md`` verbatim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from airlock.litellm_adapter import hidden_params, response_cost, served_provider

logger = logging.getLogger("airlock.transparency")

MutationOp = Literal["set", "drop", "clamp", "rewrite", "inject", "redact", "suppress"]
MutationStage = Literal["pre_call", "during_call", "post_call"]


@dataclass(slots=True)
class Mutation:
    field: str
    op: MutationOp
    before: Any | None
    after: Any | None
    stage: MutationStage
    source: str
    reason: str | None = None
    count: int | None = None  # redact-only
    category: str | None = None  # redact-only

    def __post_init__(self) -> None:
        # CC-T2 invariant — redaction records can NEVER carry the matched value.
        if self.op == "redact" and (self.before is not None or self.after is not None):
            raise ValueError(
                "redact mutations must be value-free; use record_redaction()"
            )


@dataclass(slots=True)
class ServedBackend:
    provider: str | None
    api_base_host: str | None
    region: str | None
    model_id: str | None
    response_cost: float | None
    backend_kind: Literal["native", "gateway", "unknown"]


# CC-T2 header-safety allowlist: the serializer surfaces an `after` VALUE only for
# these scalar/enum fields. Everything else renders as `field=<op>` — never content.
HEADER_VALUE_FIELDS = {"model", "reasoning_effort", "fallbacks", "num_retries"}

_GATEWAY_PROVIDERS = {"bedrock", "azure", "vertex_ai", "vertex_ai_beta"}
_NATIVE_PROVIDERS = {"anthropic", "openai", "gemini"}

# litellm's streaming wrapper hardcodes custom_llm_provider="vertex_ai_beta" for the
# Vertex-Gemini handler — even for native AI-Studio gemini calls. Normalize the alias
# and disambiguate AI Studio vs Vertex by api_base host so streaming and non-streaming
# agree (CC-T3 / §3). AI Studio is served from generativelanguage.googleapis.com.
_AI_STUDIO_HOST = "generativelanguage.googleapis.com"

# Fixed set of droppable OpenAI request params — restricts detect_dropped_params so
# metadata / airlock-internal keys are never flagged (Decision 8).
_DROPPABLE_OPENAI_PARAMS = {
    "temperature",
    "top_p",
    "n",
    "stream",
    "stop",
    "max_tokens",
    "presence_penalty",
    "frequency_penalty",
    "logit_bias",
    "user",
    "response_format",
    "seed",
    "tools",
    "tool_choice",
    "reasoning_effort",
    "logprobs",
    "top_logprobs",
}


# ---------------------------------------------------------------------------
# Ledger helpers (CC-T1: one ordered list, appended in call order)
# ---------------------------------------------------------------------------
def record_mutation(
    metadata: dict,
    *,
    field: str,
    op: MutationOp,
    before: Any | None = None,
    after: Any | None = None,
    stage: MutationStage,
    source: str,
    reason: str | None = None,
) -> None:
    """Append one Mutation to ``metadata['airlock_mutations']`` (created on first use)."""
    metadata.setdefault("airlock_mutations", []).append(
        Mutation(
            field=field,
            op=op,
            before=before,
            after=after,
            stage=stage,
            source=source,
            reason=reason,
        )
    )


def record_redaction(
    metadata: dict,
    *,
    field: str,
    count: int,
    category: str,
    stage: MutationStage,
    source: str,
) -> None:
    """Append a value-free redact record — the ONLY way redactions are recorded.

    Routes through the Mutation ctor so the CC-T2 invariant is enforced (no value).
    """
    metadata.setdefault("airlock_mutations", []).append(
        Mutation(
            field=field,
            op="redact",
            before=None,
            after=None,
            stage=stage,
            source=source,
            count=count,
            category=category,
        )
    )


# ---------------------------------------------------------------------------
# Served-backend attribution (post-call counterpart to infer_provider)
# ---------------------------------------------------------------------------
def classify_backend_kind(
    provider: str | None,
) -> Literal["native", "gateway", "unknown"]:
    """Map a provider name to its backend kind (pure, no I/O).

    gateway: bedrock/azure/vertex_ai ; native: anthropic/openai/gemini ; else unknown.
    """
    if provider in _GATEWAY_PROVIDERS:
        return "gateway"
    if provider in _NATIVE_PROVIDERS:
        return "native"
    return "unknown"


def _normalize_served_provider(
    provider: str | None, api_base_host: str | None
) -> str | None:
    """Reconcile streaming/non-streaming Google provider labels (additive).

    litellm's streaming wrapper reports ``vertex_ai_beta`` for both AI-Studio and
    Vertex gemini; the non-streaming path reports ``gemini``/``vertex_ai``. Map the
    ``vertex_ai_beta`` alias to ``vertex_ai`` and, for the Google/Vertex family,
    resolve to ``gemini`` (native AI Studio) when the api_base host is AI Studio.
    """
    if provider == "vertex_ai_beta":
        provider = "vertex_ai"
    if provider in {"gemini", "vertex_ai"} and api_base_host == _AI_STUDIO_HOST:
        return "gemini"
    return provider


def attribute_served_backend(
    response: Any, *, cost_fallback: float | None = None
) -> ServedBackend | None:
    """Read the served-backend identity from the response (via ``litellm_adapter``).

    Tolerant of partial reads (CC-T3 / §4.1): provider falls back to the wrapper
    instance attribute on streams; response_cost is None at header-flush time and
    final in the log hook. Returns None only when ``response`` itself is falsy.
    All LiteLLM-internal reads route through ``airlock.litellm_adapter``.
    """
    if not response:
        return None

    hp = hidden_params(response) or {}

    # Provider (CC-T3, streaming-correct): on streams the provider is a wrapper
    # attribute, not yet in _hidden_params.
    provider = served_provider(response)

    api_base = hp.get("api_base")
    api_base_host = urlsplit(api_base).hostname if api_base else None

    # Reconcile streaming (wrapper attr → vertex_ai_beta) with non-streaming so the
    # same backend reports the same served provider (AI Studio ⇒ gemini/native).
    provider = _normalize_served_provider(provider, api_base_host)

    region = hp.get("region_name")
    model_id = (
        hp.get("model_id")
        or hp.get("litellm_model_name")
        or hp.get("received_model_id")
    )

    cost = response_cost(response, fallback=cost_fallback)

    backend_kind = classify_backend_kind(provider)

    return ServedBackend(
        provider=provider,
        api_base_host=api_base_host,
        region=region,
        model_id=model_id,
        response_cost=cost,
        backend_kind=backend_kind,
    )


def detect_dropped_params(data: dict, model: str, provider: str) -> list[str]:
    """Client OpenAI params present in ``data`` but unsupported by the provider.

    Pure local lookup against ``litellm.get_supported_openai_params`` (no network).
    Best-effort: returns [] if the lookup raises for an unknown model/provider.
    """
    try:
        import litellm

        raw = litellm.get_supported_openai_params(
            model=model, custom_llm_provider=provider
        )
    except Exception:
        return []

    # None ⇒ support could not be determined (unknown model/provider). Best-effort:
    # treat as unknown and flag nothing rather than flagging every param.
    if raw is None:
        return []
    supported = set(raw)

    return [
        key for key in data if key in _DROPPABLE_OPENAI_PARAMS and key not in supported
    ]


# ---------------------------------------------------------------------------
# Header serializers (allowlist-aware, content-safe, byte-bounded)
# ---------------------------------------------------------------------------
def _header_safe(value: str) -> str:
    """Strip CR and LF from a value so it cannot inject new header lines."""
    return str(value).replace("\r", "").replace("\n", "")


def _mutation_token(m: Mutation) -> str:
    field = _header_safe(m.field)
    if m.op == "redact":
        return f"{field}=redacted({m.count})"
    if m.op == "suppress":
        return f"{field}=suppressed"
    if m.field in HEADER_VALUE_FIELDS and m.op in {"set", "clamp", "rewrite"}:
        return f"{field}={_header_safe(str(m.after))}"
    # inject / rewrite|drop on non-allowlisted fields / any non-allowlisted field
    # → op only, never content.
    return f"{field}={m.op}"


def mutations_header(ledger: list[Mutation], budget_bytes: int = 256) -> str:
    """Render the ledger to a compact, content-safe, byte-bounded header value."""
    tokens = [_mutation_token(m) for m in ledger]
    full = ";".join(tokens)
    if len(full.encode("utf-8")) <= budget_bytes:
        return full

    n = len(tokens)
    # Keep as many leading tokens as fit alongside a trailing `…+N more` suffix.
    for k in range(n - 1, -1, -1):
        dropped = n - k
        suffix = f"…+{dropped} more"
        if k == 0:
            candidate = suffix
        else:
            candidate = ";".join(tokens[:k]) + ";" + suffix
        if len(candidate.encode("utf-8")) <= budget_bytes:
            return candidate
    # Even the bare suffix overflows the budget — omit entirely (invariant: always <= budget).
    return ""


def served_headers(s: ServedBackend | None) -> dict[str, str]:
    """X-Airlock-Served-By/-Region; {} when provider is unknown (omit, never guess)."""
    if s is None or s.provider is None:
        return {}
    headers = {"X-Airlock-Served-By": _header_safe(s.provider)}
    if s.region:
        headers["X-Airlock-Served-Region"] = _header_safe(s.region)
    return headers


# ---------------------------------------------------------------------------
# Config loader (mirrors _load_cost_tiers / configure_breaker style)
# ---------------------------------------------------------------------------
_FALSY_STRINGS = {"false", "0", "off", "no", ""}
_TRUTHY_STRINGS = {"true", "1", "on", "yes"}


def _coerce_bool(value: object) -> bool:
    """Coerce a config value to bool, treating falsy strings sanely."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _FALSY_STRINGS:
            return False
        if v in _TRUTHY_STRINGS:
            return True
    return bool(value)


# Annotated explicitly: the mixed value types otherwise infer as dict[str, object],
# which mypy then rejects as defaults for the typed TransparencyConfig fields below.
_DEFAULTS: dict[str, Any] = {
    "mutation_headers": "compact",  # off | compact | full
    "served_headers": True,
    "explain_body_optin_header": "X-Airlock-Explain",
    "attribute_accounting_to_served": True,
    "mutation_header_budget_bytes": 256,
}

_MUTATION_HEADER_MODES = {"off", "compact", "full"}


@dataclass(frozen=True)
class TransparencyConfig:
    mutation_headers: str = _DEFAULTS["mutation_headers"]
    served_headers: bool = _DEFAULTS["served_headers"]
    explain_body_optin_header: str = _DEFAULTS["explain_body_optin_header"]
    attribute_accounting_to_served: bool = _DEFAULTS["attribute_accounting_to_served"]
    mutation_header_budget_bytes: int = _DEFAULTS["mutation_header_budget_bytes"]


def load_transparency_config(config: dict | None) -> TransparencyConfig:
    """Read top-level ``config['transparency']``, apply defaults, validate (CC-T7)."""
    block = (config or {}).get("transparency") or {}
    if not isinstance(block, dict):
        logger.warning("transparency config is not a mapping, using defaults")
        block = {}

    mode = block.get("mutation_headers", _DEFAULTS["mutation_headers"])
    if mode not in _MUTATION_HEADER_MODES:
        logger.warning("Invalid transparency.mutation_headers %r, using default", mode)
        mode = _DEFAULTS["mutation_headers"]

    budget = block.get(
        "mutation_header_budget_bytes", _DEFAULTS["mutation_header_budget_bytes"]
    )
    try:
        budget = int(budget)
        if budget <= 0:
            raise ValueError
    except (TypeError, ValueError):
        logger.warning(
            "Invalid transparency.mutation_header_budget_bytes %r, using default",
            block.get("mutation_header_budget_bytes"),
        )
        budget = _DEFAULTS["mutation_header_budget_bytes"]

    served = block.get("served_headers", _DEFAULTS["served_headers"])
    accounting = block.get(
        "attribute_accounting_to_served", _DEFAULTS["attribute_accounting_to_served"]
    )
    explain = block.get(
        "explain_body_optin_header", _DEFAULTS["explain_body_optin_header"]
    )

    return TransparencyConfig(
        mutation_headers=mode,
        served_headers=_coerce_bool(served),
        explain_body_optin_header=str(explain),
        attribute_accounting_to_served=_coerce_bool(accounting),
        mutation_header_budget_bytes=budget,
    )


_configured: TransparencyConfig | None = None


def configure_transparency(config: dict | None) -> None:
    """Set the module-global config from ``load_transparency_config`` (read at startup)."""
    global _configured
    _configured = load_transparency_config(config)


def get_transparency_config() -> TransparencyConfig:
    """Return the configured value, or safe defaults if never configured (CC-T7)."""
    if _configured is None:
        return TransparencyConfig()
    return _configured
