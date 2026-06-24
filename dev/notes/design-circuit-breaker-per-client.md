# Design: Per-client circuit breaker + threshold tuning (A1 + E)

**Date:** 2026-06-23
**Status:** Implemented in 0.5.0 (branch `feat/0.5.0-resilience-admin`; see `dev/plans/runs/STATUS-0.5.0.md`).
**Scope:** `airlock/fast/state.py`, `airlock/fast/guardian.py`,
`airlock/fast/monitor.py`, `config.yaml`, `tests/test_fast_*`.
**Index:** [design-large-context-resilience-overview.md](design-large-context-resilience-overview.md)
(see §3 for cross-cutting decisions CC-1…CC-5).
**Reconciliation:** [design-resilience-and-admin-overview.md](design-resilience-and-admin-overview.md)
(CC-6 `cleared_at`, CC-7 `_half_open_probe` — **owned by this pack**, written by
the admin clear).

---

## 1. Summary

The fast subsystem's provider-protection breaker is **one-strike and globally
tuned**:

- A single provider 429 immediately quarantines that **client→provider** pair
  for **300 s** (`state.py:179-191`, `CLIENT_PROVIDER_COOLDOWN_SECONDS = 300`).
- If **≥2 distinct clients** are rate-limited within 5 min, the **entire
  provider** is quarantined for 300 s for **all** clients
  (`PROVIDER_ESCALATION_CLIENT_THRESHOLD = 2`, `state.py:619-621`).
- All constants are module-level globals (`state.py:23-29`); there is no
  per-client tuning, despite `record_rate_limit()` already accepting
  `cooldown_seconds` as a parameter (`state.py:184, 261`).

This converts a handful of genuine 429s into a sustained outage for a trusted
batch client (overview §1: 46 quarantine vs 5 real quota in the window).

This pack makes the breaker (A1) **threshold-based** instead of one-strike, and
(E) **tunable per client key**, while (A1-b) locking in the verified
"no re-arm loop" invariant with a regression test.

## 2. Hard design decisions

1. **Threshold, not one-strike (A1).** Quarantine a client→provider pair only
   after `rate_limit_threshold` 429s within `rate_limit_window_seconds`
   (default window = existing `WINDOW_SECONDS = 300`). **Default threshold = 1**
   preserves today's behavior under CC-3; operators raise it for batch clients.
2. **Per-client overrides (E).** A config map keyed by `client_id` supplies
   `{rate_limit_threshold, cooldown_seconds, escalation_exempt, disabled}`.
   Precedence per CC-2: per-client → global default → constant.
3. **`escalation_exempt` clients do not count toward provider-wide escalation.**
   A trusted batch client hammering its own quota must not quarantine the
   provider for everyone else. This directly fixes the "2 clients → everyone
   blocked" blast radius.
4. **Cooldown still uses `max(quarantine_until, now+cooldown)`** (no additive
   stacking), unchanged. Threshold gating happens *before* arming.
5. **No re-arm loop (A1-b).** Confirmed not currently possible (pre-call raises
   bypass `log_failure_event`; see overview §2). We add an explicit regression
   test asserting a pre-call quarantine block does **not** call
   `record_provider_rate_limit`, so a future refactor can't silently introduce
   the loop.
6. **Startup-read config (CC-2).** Overrides load once at startup into the
   `store`; no hot-reload in this pack.

## 3. Data shapes

`config.yaml` (new block):

```yaml
airlock_settings:
  circuit_breaker:
    # global defaults (apply when a client has no override)
    rate_limit_threshold: 1          # 429s within window before quarantine
    rate_limit_window_seconds: 300
    client_cooldown_seconds: 300
    provider_cooldown_seconds: 300
    provider_escalation_client_threshold: 2
    clients:                          # per-client-key overrides (E)
      "key:b35cf679":                 # the batch client from the incident
        rate_limit_threshold: 8       # tolerate bursts before tripping
        client_cooldown_seconds: 30   # short cooldown; it's first-party
        escalation_exempt: true       # never trip provider-wide for others
```

Env override (CC-2), JSON, same shape under `clients`/defaults:
`AIRLOCK_BREAKER_OVERRIDES='{"defaults":{...},"clients":{"key:b35cf679":{...}}}'`

Resolved per-client config object (in `state.py`):

```python
@dataclass(frozen=True)
class BreakerPolicy:
    rate_limit_threshold: int = 1
    rate_limit_window_seconds: float = 300.0
    client_cooldown_seconds: float = 300.0
    provider_cooldown_seconds: float = 300.0
    provider_escalation_client_threshold: int = 2
    escalation_exempt: bool = False
    disabled: bool = False
```

**New state fields (this pack owns them; the admin pack writes `cleared_at`).**
Add to **both** `ClientProviderState` and `ProviderState`:

```python
    cleared_at: float = 0.0       # CC-6: floor for the rate-limit window
    _half_open_probe: bool = False  # CC-7: one-probe gate after a clear
```

- **CC-6 cleared floor.** Apply the floor to **every reader of rate-limit history
  that can re-arm a breaker**, not just the client threshold:
  `recent_rate_limit_count(window)` (client→provider) **and**
  `ProviderState.impacted_clients()` (`state.py:287`, used by provider-wide
  escalation at `state.py:618`) both count only events with `t > max(now - window,
  cleared_at)` — otherwise a provider clear re-arms on pre-clear *client* history
  at the next 429 (codex BLOCK, 2026-06-23). The deque still records every 429 for
  logging. The admin **quarantine-clearing** mutators
  (`clear_provider_quarantine`, `clear_client_provider_quarantine`) set
  `cleared_at = now`; `clear_client_backoff` / `reset_model_circuit` do **not**
  touch `cleared_at` (different target state — see umbrella CC-8).
- **CC-7 half-open probe.** Today only `ModelState` has a circuit
  (`state.py:430/435`); provider/client breakers gain `_half_open_probe`, default
  off (CC-3). A probe-mode clear sets it; the next call is admitted as a probe — a
  success closes (clears `quarantine_until`), a failure re-arms via the policy
  cooldown.

## 4. Wiring

- **`state.py`**
  - Add `BreakerPolicy` + a `BreakerConfig` holder on the `store` with
    `policy_for(client_id) -> BreakerPolicy` (per-client → default).
  - `ClientProviderState.record_rate_limit` (`state.py:179-191`): gate arming on
    `recent_rate_limit_count(window) >= policy.rate_limit_threshold`; use
    `policy.client_cooldown_seconds`. (The 429 is always *recorded*; only the
    *quarantine* is threshold-gated.) `recent_rate_limit_count` already exists
    (`state.py:205-210`) — **modify it to apply the `cleared_at` floor (CC-6)** so
    a successful probe / operator clear is honored.
  - Add the `cleared_at` / `_half_open_probe` fields and the half-open
    success/failure transitions (CC-6/CC-7); the admin pack's CC-8 mutators write
    these via methods it owns.
  - `record_provider_rate_limit` (`state.py:601-633`): pass the resolved policy;
    skip escalation counting for `escalation_exempt` clients; use
    `policy.provider_cooldown_seconds` and the (possibly per-... → global)
    escalation threshold. Return the policy-derived cooldown in the result dict
    (already surfaced to `monitor`/metadata).
- **`guardian.py`** `async_pre_call_hook` Step 2.5b (`guardian.py:218-228+`):
  honor `policy.disabled` (skip the breaker entirely for a client) and use the
  per-client cooldown in the `airlock_provider_protection` metadata it emits
  (feeds workstream B's `Retry-After`).
- **`monitor.py`** `log_failure_event` (`monitor.py:195-243`): unchanged logic,
  but `record_provider_rate_limit` now consults the policy internally; the
  `cooldown_seconds` it reports comes from the policy.
- **Config load:** parse `airlock_settings.circuit_breaker` + env at startup
  (wherever the store is constructed / `runtime.py`), inject into `store`.

## 5. Tests (TDD, RED first)

`tests/test_fast_state.py`:
- threshold gating: N-1 429s within window → not quarantined; Nth → quarantined.
- window expiry: 429s older than `rate_limit_window_seconds` don't count.
- per-client override beats default beats constant (precedence).
- `escalation_exempt` client: K≥2 of them rate-limited → provider **not**
  quarantined; a non-exempt 2nd client still escalates.
- `disabled` policy → `is_quarantined` never set from 429s.
- cooldown value honored per client.

`tests/test_fast_monitor.py`:
- **A1-b regression:** extend/duplicate `test_precall_failure_skips_circuit_breaker`
  to assert `record_provider_rate_limit` is **not** called for a pre-call block
  (mock the store method, assert `call_count == 0`). Lock the invariant.

`tests/test_fast_guardian.py`:
- `disabled` client bypasses the pre-call quarantine raise.
- per-client cooldown appears in `airlock_provider_protection` metadata.

`tests/test_config*.py`: malformed `AIRLOCK_BREAKER_OVERRIDES` JSON → falls back
to defaults with a logged warning (don't crash startup).

## 6. Documentation updates

- `docs/getting-started/configuration.md`: new `airlock_settings.circuit_breaker`
  block + `AIRLOCK_BREAKER_OVERRIDES`; add to env-var table.
- `docs/guide/routing.md`: rewrite the circuit-breaker section — threshold model,
  per-client overrides, precedence, escalation exemption.
- `docs/guide/rate-limiting.md` (new, shared with B): per-client breaker behavior.
- `docs/operations.md`: per-client breaker state in `/health/circuits`; metrics.
- `docs/troubleshooting.md`: "A client key is being quarantined too aggressively"
  → how to raise its threshold / exempt it.

## 7. Out of scope / follow-ups

- Hot-reload of breaker config (restart required for now).
- Adaptive cooldown (e.g. honoring provider `Retry-After`/`x-ratelimit-reset`) —
  belongs with workstream C once those headers are captured.
- Per-client RPM/TPM *pre-emptive* throttling (vs reactive quarantine).

## 8. Related

A1's no-re-arm finding and the window evidence: overview §1–§2. The cooldown
this pack computes is consumed by [B](design-rate-limit-client-errors.md) for
`Retry-After`; the reset hints that could replace the static cooldown come from
[C](design-provider-quota-observability.md).
</content>
