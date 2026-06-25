# Design: Routing fan-out & budget-cap guardrails (A2 + A3)

**Date:** 2026-06-23
**Status:** Implemented in 0.5.0 (branch `feat/0.5.0-resilience-admin`; see `dev/plans/runs/STATUS-0.5.0.md`).
**Scope:** `config.yaml` (`router_settings.fallbacks`,
`router_settings.provider_budget_config`), `airlock/fast/guardian.py`,
`airlock/fast/state.py` (read-only reuse), docs.
**Index:** [design-large-context-resilience-overview.md](design-large-context-resilience-overview.md)
(cross-cutting CC-1…CC-5).

---

## 1. Summary

Two structural amplifiers sit in the litellm routing layer. Neither was the
proximate cause for *this* client (it pins `gpt-5.4`, so
`guardian._lock_pinned_request` sets `num_retries=0, disable_fallbacks=True` —
`guardian.py:131-141`), but both are real blast-radius risks for any unpinned
client and for cost control:

- **A2 — Fallback fan-out.** `router_settings.fallbacks` (`config.yaml:372-399`)
  re-sends a failed request to 2–3 other models (e.g.
  `gpt-5.4 → gpt-5 → gpt-5-mini → claude-sonnet`). For a **large-context** call,
  each hop re-sends the full payload, multiplying tokens/min and $ across
  providers — and can silently answer a `gpt-5.4` request from `claude-sonnet`.
- **A3 — Daily budget cliff.** `provider_budget_config` caps OpenAI and Anthropic
  at **$50/day** each (`config.yaml:356-362`). Once hit, litellm stops routing
  to that provider until the window rolls — a hard, invisible end-of-day cliff.
  (Did not fire in the incident window — no budget-exceeded errors — but it's a
  latent failure mode for exactly this kind of large-context job.)

This pack makes both **deliberate and visible** rather than accidental.

## 2. Hard design decisions

### A2 — Fallbacks

1. **Do not fan out large or pinned requests by default.** Large-context calls
   are the worst case for fan-out (cost × payload). Decision: when a request is
   pinned, keep today's behavior (no fallback). When a request exceeds a size
   threshold (`AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS`, default e.g. 60k), suppress
   fallbacks even if unpinned — fail fast with B's descriptive 429 instead of
   silently burning 3× the tokens.
2. **Never fall back across providers on a rate-limit/quota error.** Falling
   back from a rate-limited provider to another provider spreads the incident.
   Prefer same-provider cheaper model or fail fast. (Cross-provider fallback on
   *non*-rate-limit errors — e.g. ServiceUnavailable — stays allowed.)
3. **Annotate fallback usage.** When a fallback *is* used, record it in
   `metadata["airlock_model_override"]` (the mechanism already exists,
   `guardian.py:90-102`) and emit `X-Airlock-Model-Override` so the client knows
   a different model answered. (Today this header exists for routing overrides;
   extend it to litellm fallback hops.)

### A3 — Budget caps

4. **Keep the caps, make them visible and tunable.** $50/day is a safety net,
   not a bug — but it must be observable (workstream C adds
   `airlock_provider_budget_used_usd` / `_limit_usd` gauges) and documented as a
   hard stop.
5. **Warn before the cliff.** At ≥80% of a provider's daily budget, log a
   warning and (optionally) emit an `X-Airlock-Budget-State: near_limit` header,
   reusing `ProviderSpendState.recent_spend` (`state.py:147`). No new spend
   tracking needed.
6. **Per-client / per-job budget is a follow-up**, not this pack (would need
   per-key spend accounting).

## 3. Config shapes

```yaml
# config.yaml additions / annotations
airlock_settings:
  fallbacks:
    max_prompt_tokens: 60000        # suppress fallbacks above this (A2-1)
    cross_provider_on_rate_limit: false   # (A2-2)
  budgets:
    warn_ratio: 0.8                 # warn at 80% of provider_budget_config cap (A3-5)
```

Env overrides (CC-2): `AIRLOCK_FALLBACK_MAX_PROMPT_TOKENS`,
`AIRLOCK_FALLBACK_CROSS_PROVIDER_ON_RATE_LIMIT`, `AIRLOCK_BUDGET_WARN_RATIO`.
All default to **current behavior** except the new warn (CC-3): fallbacks remain
enabled as configured; only large/rate-limit cases change.

## 4. Wiring

- **A2 size/rate-limit gating:** in `guardian.async_pre_call_hook`, after model
  resolution, when prompt token estimate > `max_prompt_tokens` **or** the target
  provider is currently quarantined, set `data["disable_fallbacks"] = True`
  (reuse `_lock_pinned_request`'s field writes; factor a helper
  `_suppress_fallbacks(data, reason)`). Token estimate: reuse litellm's
  token_counter or a cheap char/4 heuristic (good enough for a guardrail).
- **A2 cross-provider-on-rate-limit:** litellm fallbacks are model-keyed, not
  provider-aware. Implement by curating `config.yaml:fallbacks` so rate-limit
  fallbacks stay **same-provider** (e.g. `gpt-5.4 → gpt-5-mini`, drop the
  `claude-sonnet` tail), plus the runtime suppression in the bullet above for
  the quarantined-provider case. Document the curation rule.
- **A2 annotation:** ensure the model-override header is emitted on litellm
  fallback (verify whether litellm exposes the actually-used deployment in the
  success callback `kwargs`; if so, set `airlock_response_headers` in
  `monitor`/`model_override_headers`).
- **A3 warn:** in `monitor.log_success_event` after `record_spend`, compare
  `store.get_provider_spend(provider).recent_spend()` to the configured cap ×
  `warn_ratio`; log + set near-limit metadata. Budget cap values read from
  `provider_budget_config` at startup.

## 5. Tests (TDD, RED first)

`tests/test_fast_guardian.py`:
- prompt over `max_prompt_tokens` → `disable_fallbacks=True` set.
- target provider quarantined → fallbacks suppressed.
- under threshold + healthy → fallbacks untouched (no behavior change, CC-3).

`tests/test_fast_monitor.py`:
- spend crosses `warn_ratio × cap` → warning logged once / near-limit metadata.
- below ratio → silent.

Config tests: env overrides parse; bad values fall back to defaults.

(Fallback *list curation* in `config.yaml` is validated by a config-lint test
asserting no rate-limit-tier fallback crosses providers, if such a lint exists;
otherwise documented as a review rule.)

## 6. Documentation updates

- `docs/guide/routing.md`: expand **Fallbacks** (trigger conditions, the
  large-request and rate-limit suppression rules, same-provider curation,
  `X-Airlock-Model-Override` on fallback) and **Provider budgets** (the $50/day
  hard stop, `warn_ratio`, what happens at the cliff, link to C's gauges).
- `docs/getting-started/configuration.md`: `airlock_settings.fallbacks` /
  `.budgets` blocks + the three new env vars in the table.
- `docs/operations.md`: budget gauges + near-limit warning log line; how to read
  them.
- `docs/troubleshooting.md`: "Requests answered by an unexpected model" →
  fallbacks + the override header; "All requests to a provider fail late in the
  day" → daily budget cap, check the spend gauge.

## 7. Out of scope / follow-ups

- Per-client / per-job budgets (needs per-key spend accounting).
- Provider-aware fallback in litellm core (we curate config instead).
- Routing strategy changes (`cost-based-routing` stays as-is).

## 8. Related

Budget visibility depends on the gauges from
[C](design-provider-quota-observability.md); fast-fail on suppressed fallback
surfaces via [B](design-rate-limit-client-errors.md)'s descriptive 429; the
quarantine signal it reads comes from
[E/A1](design-circuit-breaker-per-client.md).
</content>
