"""
Airlock Fast — Model Alias Table.

Builds a cached routing table at startup from config.yaml model definitions.
When a client sends an unrecognized model name (e.g. ``claude-sonnet-4-6``),
the table resolves it to the nearest configured alias (``claude-sonnet``)
using multi-signal scoring: token overlap, containment, provider affinity,
and version-stripped comparison.

The table is built once (at first use / config load) and logged at INFO.
Subsequent lookups are O(1) dict hits for known names, O(n) scoring for
unknown names where n = number of configured models (~20).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from airlock.capability import airlock_provider_for, normalize_provider_token

logger = logging.getLogger("airlock.fast.model_alias")

# ---------------------------------------------------------------------------
# Scoring thresholds
# ---------------------------------------------------------------------------
_AUTO_ROUTE_THRESHOLD = 0.50  # route silently at DEBUG
_WARN_THRESHOLD = 0.35  # route with WARNING (fuzzy)
# Below _WARN_THRESHOLD: no match — let LiteLLM return its 400

# Trailing qualifiers stripped first (latest, preview and anything after)
_QUALIFIER_RE = re.compile(
    r"-(latest|preview)(?:[-_].*)?$",
    re.IGNORECASE,
)
# Trailing pure-numeric version segments: -4-20250514, -4-6, -2509
_TRAILING_DIGITS_RE = re.compile(r"[-_](\d[\d._-]*\d|\d)$")
# Separator split for tokenization
_SEPARATOR_RE = re.compile(r"[-_/.]")

# Provider prefixes — shared with router.py (duplicated to avoid import)
_PROVIDER_PREFIXES = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "gemini": "gemini",
    "mistral": "mistral",
    "codestral": "mistral",
    "magistral": "mistral",
    "gemma": "vllm",
    "perplexity": "perplexity",
    "sonar": "perplexity",
    "tavily": "tavily",
}


def _infer_provider(name: str) -> str | None:
    """Map a model name to provider via prefix."""
    for prefix, provider in _PROVIDER_PREFIXES.items():
        if name.startswith(prefix):
            return provider
    return None


def _tokenize(name: str) -> set[str]:
    """Split a model name into meaningful tokens."""
    normalized = _SEPARATOR_RE.sub(" ", name.lower())
    return {t for t in normalized.split() if t}


def _strip_version(name: str) -> str:
    """Remove version suffixes to get the family core.

    Strategy: first strip known trailing qualifiers (latest, preview),
    then iteratively strip trailing pure-numeric segments from the right.
    Segments containing letters (e.g. 'pro', 'flash') are kept.
    """
    result = _QUALIFIER_RE.sub("", name)
    while True:
        m = _TRAILING_DIGITS_RE.search(result)
        if m:
            result = result[: m.start()]
        else:
            break
    return result.rstrip("-_/ ")


def _strip_provider_prefix(name: str) -> str:
    """Remove leading provider/ prefix (e.g. anthropic/claude-... -> claude-...)."""
    if "/" in name:
        return name.split("/", 1)[1]
    return name


# ---------------------------------------------------------------------------
# Alias entry — one per configured model
# ---------------------------------------------------------------------------
@dataclass
class _AliasEntry:
    """Metadata for a single configured model."""

    alias: str  # config model_name: "claude-sonnet"
    provider_model: str  # litellm_params.model: "anthropic/claude-sonnet-4-20250514"
    provider: str | None  # inferred: "anthropic"
    tokens: set[str] = field(default_factory=set)
    family_core: str = ""  # version-stripped alias

    def __post_init__(self):
        bare_model = _strip_provider_prefix(self.provider_model)
        self.tokens = _tokenize(self.alias) | _tokenize(bare_model)
        self.family_core = _strip_version(self.alias)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _score_match(query: str, entry: _AliasEntry) -> float:
    """Score how well `query` matches an alias entry. Returns 0.0–1.0."""
    query_lower = query.lower()
    alias_lower = entry.alias.lower()
    bare_provider = _strip_provider_prefix(entry.provider_model).lower()

    # --- Signal 1: Exact match (fast path) ---
    if query_lower == alias_lower or query_lower == bare_provider:
        return 1.0

    # --- Signal 2: Containment ---
    # Does the query contain the alias, or does the provider model contain the query?
    containment = 0.0
    if alias_lower in query_lower:
        # e.g. query="claude-sonnet-4-6" contains alias="claude-sonnet"
        containment = len(alias_lower) / max(len(query_lower), 1)
    elif query_lower in bare_provider:
        # e.g. query="claude-sonnet-4-6" contained in "claude-sonnet-4-20250514"?
        # Not exactly, but partial overlap matters
        containment = len(query_lower) / max(len(bare_provider), 1)

    # --- Signal 3: Version-stripped comparison ---
    query_core = _strip_version(query_lower)
    query_core_bare = _strip_provider_prefix(query_core)
    version_match = 0.0
    if query_core_bare and (
        query_core_bare == entry.family_core
        or query_core_bare == _strip_version(bare_provider)
    ):
        version_match = 1.0
    elif query_core_bare and entry.family_core in query_core_bare:
        version_match = len(entry.family_core) / max(len(query_core_bare), 1)

    # --- Signal 4: Token overlap (Jaccard-like) ---
    query_tokens = _tokenize(query)
    if query_tokens and entry.tokens:
        overlap = len(query_tokens & entry.tokens)
        union = len(query_tokens | entry.tokens)
        token_score = overlap / union if union else 0.0
    else:
        token_score = 0.0

    # --- Signal 5: Provider affinity ---
    query_provider = _infer_provider(query_lower)
    provider_match = (
        1.0 if (query_provider and query_provider == entry.provider) else 0.0
    )

    # --- Composite ---
    # Weights tuned for model naming patterns:
    #   containment + version are the strongest signals
    #   token overlap catches partial matches
    #   provider affinity prevents cross-provider misroutes
    score = (
        0.30 * containment
        + 0.30 * version_match
        + 0.20 * token_score
        + 0.20 * provider_match
    )

    # Bonus: if provider matches AND version-stripped core matches, very high confidence
    if provider_match > 0 and version_match >= 0.9:
        score = max(score, 0.85)

    # Penalty: if providers don't match and we inferred a provider for the query,
    # this is almost certainly wrong
    if query_provider and entry.provider and query_provider != entry.provider:
        score *= 0.1

    return round(min(score, 1.0), 3)


# ---------------------------------------------------------------------------
# The alias table
# ---------------------------------------------------------------------------
class ModelAliasTable:
    """Cached routing table mapping model name variants to config aliases.

    Built once from config.yaml. Provides O(1) lookups for exact matches
    and O(n) fuzzy scoring for unknown names.
    """

    def __init__(self) -> None:
        self._entries: list[_AliasEntry] = []
        self._exact: dict[str, str] = {}  # lowered name → alias
        # Provider-aware index for collision-safe prefix-strip in resolve().
        # (provider_token, bare_body) → alias  (first writer wins per pair)
        self._provider_body_alias: dict[tuple[str, str], str] = {}
        # bare_body → set of served-by provider tokens it appears under
        self._body_providers: dict[str, set[str]] = {}
        # variant keys dropped in pass 2 because ≥2 entries claimed them — these
        # must NOT leak into the fuzzy slow path (silent cross-provider repoint).
        self._ambiguous_variants: set[str] = set()
        self._loaded = False

    def load_from_config(self, config_path: str | Path | None = None) -> None:
        """Parse config.yaml and build the routing table."""
        # Reset all table state up front so every early return (missing file,
        # parse error, non-dict config) leaves a cleanly-empty table — never a
        # stale carry-over from a previous successful load.
        self._entries = []
        self._exact = {}
        self._provider_body_alias = {}
        self._body_providers = {}
        self._ambiguous_variants = set()

        if config_path is None:
            config_path = os.getenv("AIRLOCK_CONFIG", "config.yaml")
        path = Path(config_path)
        if not path.is_file():
            logger.warning("Config file not found at %s — alias table empty", path)
            self._loaded = True
            return

        try:
            with open(path) as f:
                cfg = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            logger.error("Failed to load config for alias table: %s", exc)
            self._loaded = True
            return

        if not isinstance(cfg, dict):
            logger.warning("Config is not a dict — alias table empty")
            self._loaded = True
            return

        model_list = cfg.get("model_list") or []

        # --- Pass 1: explicit model_name keys are authoritative + immutable ---
        explicit_keys: set[str] = set()
        # variant key → set of aliases that claim it (collision detection)
        variant_claims: dict[str, set[str]] = {}

        for item in model_list:
            alias = item.get("model_name", "")
            params = item.get("litellm_params") or {}
            provider_model = params.get("model", "")
            if not alias or not provider_model:
                continue

            provider = _infer_provider(alias) or _infer_provider(
                _strip_provider_prefix(provider_model)
            )
            entry = _AliasEntry(
                alias=alias,
                provider_model=provider_model,
                provider=provider,
            )
            self._entries.append(entry)

            alias_lower = alias.lower()
            self._exact[alias_lower] = alias
            explicit_keys.add(alias_lower)

            # Provider-aware index: served-by token (NOT the alias prefix) keyed
            # against the bare provider-model body and its version-stripped form.
            token = airlock_provider_for(item)
            bare = _strip_provider_prefix(provider_model).lower()
            bare_core = _strip_version(bare)
            if token:
                for body in {bare, bare_core}:
                    if not body:
                        continue
                    self._provider_body_alias.setdefault((token, body), alias)
                    self._body_providers.setdefault(body, set()).add(token)

            # Collect candidate variant keys (resolved in Pass 2 only if unique).
            alias_core = _strip_version(alias_lower)
            for key in (bare, alias_core, bare_core):
                if key:
                    variant_claims.setdefault(key, set()).add(alias)

        # --- Pass 2: add variant keys only when not explicit AND unambiguous ---
        for key, claimers in variant_claims.items():
            if key in explicit_keys:
                continue
            if len(claimers) == 1:
                self._exact[key] = next(iter(claimers))
            else:
                self._ambiguous_variants.add(key)
                logger.debug(
                    "model_alias_ambiguous_variant %s claimed by %s — dropped",
                    key,
                    sorted(claimers),
                )

        self._loaded = True
        self._log_table()

    def _log_table(self) -> None:
        """Log the routing table at INFO for startup visibility."""
        if not self._entries:
            logger.info("Model alias table: empty (no models configured)")
            return

        lines = ["Model alias routing table:"]
        for entry in self._entries:
            # Collect all exact keys that map to this alias
            variants = sorted(
                k
                for k, v in self._exact.items()
                if v == entry.alias and k != entry.alias.lower()
            )
            variant_str = ", ".join(variants) if variants else "(no variants)"
            lines.append(
                f"  {entry.alias:30s} -> {entry.provider_model:45s} "
                f"variants=[{variant_str}]"
            )
        logger.info("\n".join(lines))

    def resolve(self, model_name: str) -> str | None:
        """Resolve a model name to its configured alias.

        Returns the alias string if matched, or None if no confident match.
        """
        if not self._loaded:
            self.load_from_config()

        # Batch/file routes (/v1/batches, /v1/files) carry no top-level model —
        # nothing to resolve, and model_name may be None/non-str. Bail safely.
        if not isinstance(model_name, str) or not model_name:
            return None

        lower = model_name.lower()

        # Fast path: exact match (covers alias, bare provider model, version-stripped)
        if lower in self._exact:
            return self._exact[lower]

        # Provider-aware prefix-strip — handles native + airlock-prefixed forms
        # (openai/claude-haiku, gemini/gemini-3.5-flash, vertex_ai/gemini-3.5-flash,
        # aistudio/…, vertex/…). Normalises the leading prefix to a served-by
        # token so a body shared by multiple providers routes to the RIGHT entry
        # instead of last-write-wins.
        if "/" in lower:
            prefix, bare = lower.split("/", 1)
            token = normalize_provider_token(prefix)

            # 1. exact (provider_token, bare_body) hit → the right deployment.
            resolved = self._provider_body_alias.get((token, bare))
            if resolved is not None:
                self._exact[lower] = resolved
                logger.debug("model_alias_prefix_strip %s -> %s", model_name, resolved)
                return resolved

            # 2. known body: single-provider → resolve regardless of prefix;
            #    multi-provider with a non-disambiguating prefix → None (no fuzzy,
            #    no cache) so a silent fuzzy pick can't reintroduce a repoint.
            providers = self._body_providers.get(bare)
            if providers is not None:
                if len(providers) == 1:
                    only = next(iter(providers))
                    resolved = self._provider_body_alias[(only, bare)]
                    self._exact[lower] = resolved
                    logger.debug(
                        "model_alias_prefix_strip %s -> %s", model_name, resolved
                    )
                    return resolved
                logger.debug(
                    "model_alias_ambiguous_prefix %s (body=%s providers=%s) -> None",
                    model_name,
                    bare,
                    sorted(providers),
                )
                return None

            # 3. bare matches a collision-safe alias/variant key (e.g. an alias
            #    name that isn't itself a litellm body) → resolve.
            if bare in self._exact:
                resolved = self._exact[bare]
                self._exact[lower] = resolved  # cache for O(1) on repeat calls
                logger.debug("model_alias_prefix_strip %s -> %s", model_name, resolved)
                return resolved

            # 4. bare is an ambiguous (dropped) multi-claimer variant → None;
            #    never let it fall into fuzzy (would silently repoint + cache).
            if bare in self._ambiguous_variants:
                return None

        # An ambiguous (dropped) body with NO disambiguating prefix must NOT leak
        # into fuzzy scoring — a near-exact body match would silently pick the
        # first claimer (cross-provider repoint). Return None, do NOT cache.
        if lower in self._ambiguous_variants:
            return None

        # Slow path: fuzzy scoring against all entries
        best_score = 0.0
        best_alias: str | None = None
        for entry in self._entries:
            score = _score_match(model_name, entry)
            if score > best_score:
                best_score = score
                best_alias = entry.alias

        if best_score >= _WARN_THRESHOLD and best_alias is not None:
            self._exact[lower] = best_alias
            if best_score >= _AUTO_ROUTE_THRESHOLD:
                logger.debug(
                    "model_alias_resolved %s -> %s (score=%.3f)",
                    model_name,
                    best_alias,
                    best_score,
                )
            else:
                logger.warning(
                    "model_alias_fuzzy %s -> %s (score=%.3f, below auto threshold)",
                    model_name,
                    best_alias,
                    best_score,
                )
            return best_alias

        logger.debug(
            "model_alias_no_match %s (best=%s score=%.3f)",
            model_name,
            best_alias,
            best_score,
        )
        return None


# Module-level singleton
alias_table = ModelAliasTable()
