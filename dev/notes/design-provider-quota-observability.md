# Design: Provider quota / rate-limit observability (C)

**Date:** 2026-06-23
**Status:** Design proposal (pre-implementation).
**Scope:** `airlock/fast/monitor.py`, `airlock/fast/state.py`,
`airlock/callbacks/enterprise_logger.py`, `airlock/callbacks/metrics.py`,
`airlock/tui/screens/*`, `config.yaml`.
**Index:** [design-large-context-resilience-overview.md](design-large-context-resilience-overview.md)
**Reconciliation:** [design-resilience-and-admin-overview.md](design-resilience-and-admin-overview.md)
(CC-9 `record_type` — **introduced by this pack**; the admin pack adds the
`admin_action` branch).
(cross-cutting CC-1…CC-5).

---

## 1. Summary

We are flying blind on upstream quota headroom. When OpenAI returns
"exceeded your current quota," Airlock has no prior signal that the limit was
approaching, and no record afterward of how close we were. The account is
**OpenAI Tier 4**, so the ceilings are known — but it's the live *remaining*
values that explain and predict 429s.

**Confirmed available (Explore agent), but currently captured nowhere:**

- Per-call provider rate-limit headers at
  `response._hidden_params["additional_headers"]` (normalized to `x-ratelimit-*`
  for all providers by litellm's `process_response_headers`;
  `core_helpers.py:245-267`). OpenAI:
  `x-ratelimit-remaining-tokens|requests`, `x-ratelimit-limit-*`,
  `x-ratelimit-reset-*`. Anthropic mapped from `anthropic-ratelimit-*`
  (`anthropic/common_utils.py:992-1016`).
- On a 429, `exc.response.headers` carries the same (`exceptions.py:341-346`).
- Per-provider **USD spend is already tracked** (`ProviderSpendState`,
  `state.py:138-149`; fed at `monitor.py:117`) — just not surfaced against the
  $50/day cap (A3).

Current gaps: `monitor.py:113` reads only `response_cost`; `enterprise_logger`
serializes `response_obj.model_dump()` which **excludes `_hidden_params`**
(`enterprise_logger.py:473`), so the headers never hit the logs.

## 2. Hard design decisions

1. **Capture in the monitor callback, both paths.** On success read
   `response._hidden_params["additional_headers"]`; on failure read
   `exc.response.headers`. Parse the standardized `x-ratelimit-*` keys.
2. **Track remaining headroom in `state.py`.** Add a small
   `ProviderRateLimitState` (latest `remaining_tokens`, `remaining_requests`,
   `limit_*`, `reset_*`, observed-at) per provider, updated every call. Cheap,
   in-memory, mirrors existing `ProviderSpendState`.
3. **Three surfaces, observe-only (CC-5):**
   - **Metrics** (`metrics.py`): gauges
     `airlock_provider_ratelimit_remaining_tokens{provider}`,
     `…_remaining_requests{provider}`, and
     `airlock_provider_budget_used_usd{provider}` /
     `airlock_provider_budget_limit_usd{provider}` (A3 visibility).
   - **Structured log enrichment**: add a compact
     `provider_ratelimit: {remaining_tokens, remaining_requests, reset_tokens}`
     to the request-log record (since `model_dump()` drops `_hidden_params`,
     copy the parsed values into the record explicitly in `_build_record`).
   - **TUI**: a headroom line on the Overview/Guards screen (provider →
     remaining tokens/requests, % of limit, spend vs cap).
4. **Optional response passthrough.** Behind a flag
   (`AIRLOCK_EXPOSE_PROVIDER_RATELIMIT_HEADERS`, default off), echo
   `x-ratelimit-remaining-*` to the client via the existing
   `airlock_response_headers` metadata path (`model_override_headers.py`). Off
   by default to avoid leaking infra posture.
5. **Feeds adaptive cooldown later.** The captured `reset_*` values are what a
   future adaptive breaker (E follow-up) and B's `Retry-After` should prefer over
   a static cooldown. This pack only *captures and exposes*; consumption is a
   follow-up.

## 3. Data shapes

```python
@dataclass
class ProviderRateLimitState:
    provider: str
    remaining_tokens: int | None = None
    remaining_requests: int | None = None
    limit_tokens: int | None = None
    limit_requests: int | None = None
    reset_tokens_seconds: float | None = None     # parsed from x-ratelimit-reset-tokens
    reset_requests_seconds: float | None = None
    observed_at: float = 0.0
```

Parser helper (pure, unit-testable):

```python
def parse_ratelimit_headers(headers: dict[str, str]) -> dict:
    # tolerant: missing keys → None; "1s"/"6m0s"/ISO → seconds
```

## 4. Wiring

- **`monitor.py`** `log_success_event` (after cost, ~line 113): extract
  `getattr(response_obj, "_hidden_params", {}).get("additional_headers", {})`,
  parse, `store.record_provider_ratelimit(provider, parsed, now)`.
  `log_failure_event` (~line 195): on `RateLimitError`, parse
  `exc.response.headers` likewise (gives the *exhausted* snapshot — valuable).
- **`state.py`:** `ProviderRateLimitState` + `record_provider_ratelimit` +
  `get_provider_ratelimit`; plus `recent_spend` already exists for A3 budget
  gauges.
- **`metrics.py`:** register the gauges; set them from the monitor (or a tiny
  collector reading `store`).
- **`enterprise_logger.py` `_build_record`:** add the parsed
  `provider_ratelimit` block (explicit copy — do **not** rely on `model_dump`).
  **Also introduce `record_type` here (CC-9):** stamp request records with
  `record_type: "request"`. This pack owns the field because it already edits both
  `_build_record` and the `ingest_jsonl_record` path; the admin pack later adds
  only a `record_type == "admin_action"` branch (a record with no `model`, which
  the current ingest early-return at `state.py:715` would otherwise drop). Treat an
  absent `record_type` as `"request"` for back-compat with existing logs.
- **TUI** `tui/screens/overview.py` / `guards.py`: render headroom + spend/cap.
- **Config:** `AIRLOCK_EXPOSE_PROVIDER_RATELIMIT_HEADERS` (bool, default off).

## 5. Tests (TDD, RED first)

`tests/test_ratelimit_parse.py`: header parsing — OpenAI keys, Anthropic-mapped
keys, missing keys → None, duration formats (`"1s"`, `"6m0s"`, ISO) → seconds.

`tests/test_fast_monitor.py`:
- success with `_hidden_params.additional_headers` → `store` headroom updated.
- failure with `exc.response.headers` → headroom updated (exhausted snapshot).
- absent `_hidden_params` → no crash, state untouched.

`tests/test_metrics.py`: gauges reflect the latest `store` values.

`tests/test_enterprise_logger.py`: record contains `provider_ratelimit` when
headers present; absent cleanly when not.

## 6. Documentation updates

- `docs/guide/provider-observability.md` (**new**): what's captured, where to
  see it (metrics names, TUI, logs), how to read headroom, the optional
  passthrough flag, and the A3 budget gauges.
- `docs/operations.md`: new Prometheus gauges in the metrics table; monitoring/
  alerting suggestions (alert when `remaining_tokens` < X% or spend > Y% of cap).
- `docs/getting-started/configuration.md`:
  `AIRLOCK_EXPOSE_PROVIDER_RATELIMIT_HEADERS` in the env-var table.
- `docs/guide/tui.md`: the new headroom/spend surface.
- `docs/troubleshooting.md`: "Frequent provider 429s" → check headroom gauges /
  spend-vs-cap before blaming the breaker.

## 7. Out of scope / follow-ups

- **Consuming** reset hints for adaptive cooldown / `Retry-After` (follow-up that
  ties C → B and C → E).
- Pre-emptive client throttling when headroom is low.
- Historical headroom time-series beyond Prometheus scrape (no new store).

## 8. Related

Budget-cap gauges here give A3 its visibility
([design-routing-fanout-guardrails.md](design-routing-fanout-guardrails.md));
captured `reset_*`/`Retry-After` is the upgrade path for
[B](design-rate-limit-client-errors.md) and the adaptive-cooldown follow-up of
[E](design-circuit-breaker-per-client.md).
</content>
