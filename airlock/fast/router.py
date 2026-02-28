"""
Airlock Fast Router — Intelligent model routing via client directives.

Clients influence model selection by passing directives in ``metadata.airlock``:

    {"model": "claude-sonnet", "metadata": {"airlock": {
        "session_id": "abc123",    # pin to a model for session duration
        "cost_tier": "low",        # restrict to low-cost models
        "prefer_provider": "anthropic"  # soft tiebreaker
    }}}

Directive application order:
  1. Session affinity — existing sessions pin to their recorded model
  2. Cost tier — restrict to models in the requested cost tier
  3. Provider preference — soft tiebreaker among viable models
  4. Budget awareness — proactively avoid providers near daily budget

Called from guardian.py between threat assessment and circuit breaker.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

from .state import store

logger = logging.getLogger("airlock.fast.router")

# ---------------------------------------------------------------------------
# Default cost tiers
# ---------------------------------------------------------------------------
_DEFAULT_COST_TIERS: dict[str, list[str]] = {
    "low": [
        "claude-haiku", "gemini-flash", "gemini-flash-lite",
        "gpt-4o-mini", "mistral-small",
    ],
    "medium": [
        "claude-sonnet", "gemini-pro", "gpt-4o",
        "mistral-large", "codestral",
    ],
    "high": [
        "claude-opus", "gemini-3-pro", "gemini-3.1-pro",
        "magistral-medium",
    ],
}

_DEFAULT_SESSION_TTL = 3600  # 1 hour

_DEFAULT_PROVIDER_BUDGETS: dict[str, float] = {
    "anthropic": 50.0,
    "openai": 50.0,
    "gemini": 25.0,
    "mistral": 25.0,
}

_BUDGET_WARN_THRESHOLD = 0.9  # 90% of budget triggers proactive swap

# ---------------------------------------------------------------------------
# Smart complexity classifier — all O(n) string ops, no ML, no dependencies
# ---------------------------------------------------------------------------
_REASONING_KEYWORDS = frozenset({
    "analyze", "analyse", "compare", "contrast", "evaluate", "explain why",
    "implement", "debug", "optimize", "refactor", "design", "architect",
    "trade-off", "tradeoff", "pros and cons", "step by step",
    "root cause", "diagnose", "synthesize", "critique",
})

_MULTI_STEP_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+[.)]\s|[-*]\s)"
    r"|(?:first|then|next|finally|step \d|after that|additionally)",
    re.IGNORECASE,
)

_COMPLEXITY_WEIGHTS = {
    "token_count": 0.30,
    "code_blocks": 0.25,
    "reasoning": 0.20,
    "multi_step": 0.10,
    "vocab_rich": 0.10,
    "sentence_len": 0.05,
}

_TIER_MAP = {"simple": "low", "moderate": "medium", "complex": "high"}

_DEFAULT_SMART_THRESHOLDS = (0.30, 0.60)


@dataclass
class ComplexityResult:
    """Result of smart complexity classification."""

    complexity: str  # "simple", "moderate", "complex"
    score: float  # 0.0–1.0 composite
    tier: str  # mapped cost tier: "low", "medium", "high"
    features: dict[str, float] = field(default_factory=dict)


# Provider inference — prefix heuristic (duplicated from monitor to avoid
# circular imports; both modules import state).
_PROVIDER_PREFIXES = {
    "claude": "anthropic",
    "gpt": "openai",
    "gemini": "gemini",
    "mistral": "mistral",
    "codestral": "mistral",
    "magistral": "mistral",
}


# ---------------------------------------------------------------------------
# Smart classifier helpers
# ---------------------------------------------------------------------------
def _load_smart_thresholds() -> tuple[float, float]:
    """Load (simple_max, complex_min) from env or defaults."""
    raw = os.environ.get("AIRLOCK_SMART_THRESHOLDS")
    if not raw:
        return _DEFAULT_SMART_THRESHOLDS
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and len(parsed) == 2:
            return (float(parsed[0]), float(parsed[1]))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    logger.warning("Invalid AIRLOCK_SMART_THRESHOLDS, using defaults")
    return _DEFAULT_SMART_THRESHOLDS


def _extract_text(data: dict) -> str:
    """Extract concatenated user text from messages array."""
    messages = data.get("messages") or []
    parts: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return " ".join(parts)


def _sigmoid(x: float, midpoint: float, steepness: float = 0.05) -> float:
    """Smooth 0-1 mapping centered at midpoint."""
    exp_val = -steepness * (x - midpoint)
    # Clamp to avoid overflow
    exp_val = max(-500, min(500, exp_val))
    return 1.0 / (1.0 + 2.718281828 ** exp_val)


def classify_complexity(text: str) -> ComplexityResult:
    """Classify prompt complexity using six weighted text features.

    Returns a ComplexityResult with composite score 0–1 mapped to a
    complexity tier. Runs in ~50μs — well under the 150μs budget.
    """
    # Edge case: empty/whitespace → moderate (fail-to-medium)
    stripped = text.strip()
    if not stripped:
        return ComplexityResult(
            complexity="moderate", score=0.45, tier="medium",
            features={k: 0.0 for k in _COMPLEXITY_WEIGHTS},
        )

    text_lower = stripped.lower()
    words = stripped.split()
    word_count = len(words)

    # Feature 1: Token count — sigmoid from 20–150 words
    f_token = _sigmoid(word_count, 85.0, 0.04)

    # Feature 2: Code blocks — fenced ``` or inline backticks
    fenced = text.count("```")
    if fenced >= 2:
        f_code = 1.0
    elif fenced == 1:
        f_code = 0.6
    elif "`" in text:
        f_code = 0.3
    else:
        f_code = 0.0

    # Feature 3: Reasoning keywords — saturates at 3 hits
    keyword_hits = sum(1 for kw in _REASONING_KEYWORDS if kw in text_lower)
    f_reasoning = min(keyword_hits / 3.0, 1.0)

    # Feature 4: Multi-step indicators
    step_matches = len(_MULTI_STEP_RE.findall(text))
    f_multi_step = min(step_matches / 3.0, 1.0)

    # Feature 5: Vocabulary richness (needs ≥10 words)
    if word_count >= 10:
        lower_words = [w.lower() for w in words]
        f_vocab = len(set(lower_words)) / len(lower_words)
    else:
        f_vocab = 0.0

    # Feature 6: Sentence length (weak tiebreaker)
    sentences = [s.strip() for s in re.split(r"[.!?]+", stripped) if s.strip()]
    avg_sentence_words = (
        sum(len(s.split()) for s in sentences) / len(sentences)
        if sentences else 0
    )
    f_sentence = min(avg_sentence_words / 25.0, 1.0)

    features = {
        "token_count": round(f_token, 3),
        "code_blocks": round(f_code, 3),
        "reasoning": round(f_reasoning, 3),
        "multi_step": round(f_multi_step, 3),
        "vocab_rich": round(f_vocab, 3),
        "sentence_len": round(f_sentence, 3),
    }

    # Composite weighted score
    score = sum(
        _COMPLEXITY_WEIGHTS[k] * features[k] for k in _COMPLEXITY_WEIGHTS
    )
    score = round(min(max(score, 0.0), 1.0), 3)

    # Map to complexity tier
    simple_max, complex_min = _load_smart_thresholds()
    if score < simple_max:
        complexity = "simple"
    elif score >= complex_min:
        complexity = "complex"
    else:
        complexity = "moderate"

    tier = _TIER_MAP[complexity]
    return ComplexityResult(
        complexity=complexity, score=score, tier=tier, features=features,
    )


# ---------------------------------------------------------------------------
# Env-var loaders
# ---------------------------------------------------------------------------
def _load_cost_tiers() -> dict[str, list[str]]:
    raw = os.environ.get("AIRLOCK_COST_TIERS")
    if not raw:
        return dict(_DEFAULT_COST_TIERS)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid AIRLOCK_COST_TIERS JSON, using defaults")
        return dict(_DEFAULT_COST_TIERS)


def _load_session_ttl() -> int:
    raw = os.environ.get("AIRLOCK_SESSION_TTL")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_SESSION_TTL


def _load_provider_budgets() -> dict[str, float]:
    raw = os.environ.get("AIRLOCK_PROVIDER_BUDGETS")
    if not raw:
        return dict(_DEFAULT_PROVIDER_BUDGETS)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Invalid AIRLOCK_PROVIDER_BUDGETS JSON, using defaults")
        return dict(_DEFAULT_PROVIDER_BUDGETS)


# ---------------------------------------------------------------------------
# Provider inference
# ---------------------------------------------------------------------------
def infer_provider(model_name: str) -> str | None:
    """Map a model alias to its provider name via prefix matching."""
    for prefix, provider in _PROVIDER_PREFIXES.items():
        if model_name.startswith(prefix):
            return provider
    return None


# ---------------------------------------------------------------------------
# Individual directive handlers
# ---------------------------------------------------------------------------
def _apply_cost_tier(
    tier: str, model: str
) -> tuple[str, str | None]:
    """Restrict model to the requested cost tier.

    Returns (model, reason) — reason is None if no change.
    """
    if tier == "any":
        return model, None

    tiers = _load_cost_tiers()
    tier_models = tiers.get(tier)
    if not tier_models:
        logger.warning("Unknown cost tier %r, ignoring", tier)
        return model, None

    if model in tier_models:
        return model, None

    # Swap to first model in the tier list
    new_model = tier_models[0]
    return new_model, f"cost_tier({tier}\u2192{new_model})"


def _apply_provider_preference(
    provider: str, model: str, candidates: list[str] | None
) -> tuple[str, str | None]:
    """Soft tiebreaker: prefer models from the requested provider.

    Returns (model, reason) — reason is None if no change.
    """
    current_provider = infer_provider(model)
    if current_provider == provider:
        return model, None

    # Look for a candidate from the preferred provider
    search_pool = candidates if candidates else []
    for candidate in search_pool:
        if infer_provider(candidate) == provider:
            return candidate, f"prefer_provider({provider}\u2192{candidate})"

    return model, None


def _apply_budget_awareness(
    model: str, candidates: list[str] | None
) -> tuple[str, str | None]:
    """Proactively swap away from providers near their daily budget.

    Returns (model, reason) — reason is None if no change.
    """
    provider = infer_provider(model)
    if not provider:
        return model, None

    budgets = _load_provider_budgets()
    budget_limit = budgets.get(provider)
    if not budget_limit:
        return model, None

    spend = store.get_provider_spend(provider).recent_spend()
    if spend < budget_limit * _BUDGET_WARN_THRESHOLD:
        return model, None

    # Current provider is near budget — find an alternative
    search_pool = candidates if candidates else []
    for candidate in search_pool:
        alt_provider = infer_provider(candidate)
        if not alt_provider or alt_provider == provider:
            continue
        alt_budget = budgets.get(alt_provider)
        if not alt_budget:
            # No budget configured for this provider — safe to use
            return candidate, f"budget({provider}@{spend:.1f}/{budget_limit:.1f}\u2192{candidate})"
        alt_spend = store.get_provider_spend(alt_provider).recent_spend()
        if alt_spend < alt_budget * _BUDGET_WARN_THRESHOLD:
            return candidate, f"budget({provider}@{spend:.1f}/{budget_limit:.1f}\u2192{candidate})"

    logger.warning(
        "All providers near budget, staying on %s (%s: $%.1f/$%.1f)",
        model, provider, spend, budget_limit,
    )
    return model, None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def apply_routing(data: dict) -> dict:
    """Apply intelligent routing directives from metadata.airlock.

    Called from guardian.py between threat assessment and circuit breaker.
    Mutates ``data["model"]`` and attaches ``metadata.airlock_routing``.
    """
    metadata = data.get("metadata") or {}
    airlock_meta = metadata.get("airlock") or {}

    original_model = data.get("model", "unknown")

    # ---- Smart model: classify complexity before directive processing ----
    if original_model == "smart":
        if not airlock_meta:
            airlock_meta = {}
            metadata["airlock"] = airlock_meta
            data.setdefault("metadata", metadata)

        text = _extract_text(data)
        result = classify_complexity(text)

        # Inject cost tier so existing tier logic picks the model
        airlock_meta["cost_tier"] = result.tier
        # Set a concrete default so downstream logic has a real model
        data["model"] = "claude-sonnet"
        original_model = "smart"

        # Stash classification for observability (slow analyzer reads this)
        routing_meta = data.setdefault("metadata", {}).setdefault(
            "airlock_routing", {},
        )
        routing_meta["smart_classify"] = {
            "complexity": result.complexity,
            "score": result.score,
            "features": result.features,
        }

    if not airlock_meta:
        return data

    model = data.get("model", original_model)
    reasons: list[str] = []

    session_id = airlock_meta.get("session_id")
    cost_tier = airlock_meta.get("cost_tier")
    prefer_provider = airlock_meta.get("prefer_provider")

    # Determine candidate pool (cost-tier-filtered if applicable)
    tier_candidates: list[str] | None = None
    if cost_tier and cost_tier != "any":
        tiers = _load_cost_tiers()
        tier_candidates = tiers.get(cost_tier)

    # ---- 1. Session affinity ----
    session_ttl = _load_session_ttl()
    if session_id:
        existing = store.get_session(session_id)
        if existing and (time.time() - existing.last_used) < session_ttl:
            # Active session — pin to recorded model
            model = existing.model
            existing.last_used = time.time()
            reasons.append(f"session_pin({existing.model})")
        else:
            # New or expired session — apply other directives first,
            # then pin the result
            if cost_tier:
                model, reason = _apply_cost_tier(cost_tier, model)
                if reason:
                    reasons.append(reason)

            if prefer_provider:
                model, reason = _apply_provider_preference(
                    prefer_provider, model, tier_candidates,
                )
                if reason:
                    reasons.append(reason)

            model, reason = _apply_budget_awareness(model, tier_candidates)
            if reason:
                reasons.append(reason)

            # Pin the resolved model
            store.set_session(session_id, model)
            reasons.append(f"session_new({model})")
    else:
        # No session — apply directives in order
        if cost_tier:
            model, reason = _apply_cost_tier(cost_tier, model)
            if reason:
                reasons.append(reason)

        if prefer_provider:
            model, reason = _apply_provider_preference(
                prefer_provider, model, tier_candidates,
            )
            if reason:
                reasons.append(reason)

        model, reason = _apply_budget_awareness(model, tier_candidates)
        if reason:
            reasons.append(reason)

    # ---- Attach routing metadata ----
    changed = model != original_model
    if reasons:
        data["model"] = model
        metadata = data.setdefault("metadata", {})
        # Merge into existing routing_meta (smart_classify may already be set)
        routing_meta = metadata.setdefault("airlock_routing", {})
        routing_meta.update({
            "original_model": original_model,
            "routed_model": model,
            "changed": changed,
            "reasons": reasons,
        })
        if session_id:
            routing_meta["session_id"] = session_id
        if cost_tier:
            routing_meta["cost_tier"] = cost_tier

        if changed:
            logger.info(
                "routed %s\u2192%s reasons=%s",
                original_model, model, reasons,
            )

    return data
