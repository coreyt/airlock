# Design: Large-context resilience — overview & index

**Date:** 2026-06-23
**Status:** Design proposal (pre-implementation). Six workstreams, grouped into
four design notes + this index.
**Scope:** `airlock/fast/{guardian,monitor,state}.py`, a new proxy exception
handler, `airlock/callbacks/*`, `config.yaml`, docs.
**Audience:** the implementer of the circuit-breaker / rate-limit hardening pack.

---

## 1. Summary — the problem, grounded in the logs

A client running large-context jobs (extraction/synthesis over big multi-session
contexts) saw calls to **both** gpt-5.4 and claude-sonnet "return empty." We
traced it through the structured request logs (`logs/airlock-*.jsonl`). The
findings overturned the initial "reasoning ate the token budget" hypothesis:

- **Not** reasoning-budget exhaustion (`reasoning_tokens == 0` on every empty),
  **not** context-window overflow (≈0 `context_length_exceeded`), **not**
  content stripping by guardrails (the reasoning stripper only targets
  `kimi-dev`; the response scanner defaults to observe-only).
- The empties are **rate-limit / quota failures**, and the **dominant proximate
  cause is Airlock's own circuit breaker**.

Evidence from the actual late-night window (`airlock-2026-06-22/23.jsonl`):

```
46  airlock_quarantine      ← Airlock circuit breaker blocked the call pre-flight
 5  openai_account_quota    ← real OpenAI 429 ("exceeded your current quota")
 3  ServiceUnavailable
 1  BadRequest (unsupported param)
```

The trace: **one** real provider 429 lands, the client retries the large call
**every 1–9 seconds**, and every retry bounces off a **300 s** Airlock
quarantine it has no way to know about. A handful of genuine 429s (5) get
amplified into ~46 failures. The char counts differ between retries → these are
**distinct client requests in a tight loop**, not litellm re-sends.

### Why large calls specifically

1. They consume the most tokens/min, so a large call is usually the one that
   trips the upstream limit and arms the breaker.
2. They are slow, so many are in flight when the breaker opens.
3. During the open window, the expensive large retries are exactly what gets
   thrown away pre-flight.

## 2. Root-cause structure (what the design fixes)

| ID | Structural issue | File evidence | Workstream |
|----|------------------|---------------|------------|
| A1 | Breaker is **one-strike**: a single 429 → 300 s quarantine; provider-wide escalation at just **2 clients** | `state.py:26-29`, `state.py:601` | [Breaker](design-circuit-breaker-per-client.md) |
| E  | Breaker cooldown/threshold are **global module constants**, not tunable per client key | `state.py:23-29`, `state.py:184,261` | [Breaker](design-circuit-breaker-per-client.md) |
| B  | Quarantine raises a **generic `RateLimitError`**; cooldown/scope/reason live only in metadata, never reach the client; **no `Retry-After`** → clients retry blindly | `guardian.py:105-128` | [Client errors](design-rate-limit-client-errors.md) |
| C  | **Zero capture** of upstream `x-ratelimit-remaining-*` headers; flying blind on quota headroom | `monitor.py:113`, headers available at `response._hidden_params["additional_headers"]` | [Quota observability](design-provider-quota-observability.md) |
| A2 | **Fallback chains** re-send the large payload to 2–3 models on failure (latent for pinned clients, live for unpinned) | `config.yaml:372-399` | [Routing guardrails](design-routing-fanout-guardrails.md) |
| A3 | **$50/day per-provider budget cap** is a hard daily cliff with no visibility | `config.yaml:356-362` | [Routing guardrails](design-routing-fanout-guardrails.md) |

A1 verification result (Explore agent, confirmed against litellm source): the
feared **re-arm loop is not currently possible** — pre-call raises route through
`post_call_failure_hook` (→ `async_post_call_failure_hook`), not the
breaker-feeding `log_failure_event`; `monitor.py` deliberately does not implement
the former, and `tests/test_fast_monitor.py::test_precall_failure_skips_circuit_breaker`
guards the `exception is None` path. **A1's job is therefore (a) one-strike →
threshold, and (b) lock the no-re-arm invariant with an explicit regression
test**, not fix an active bug.

## 3. Cross-cutting decisions (apply to all workstreams)

These are defined once here so the four detail docs stay consistent.

- **CC-1 Client identity.** All per-client behavior keys on the existing
  `client_id` from `monitor._extract_client_id` / `guardian._request_client_id`
  (`key:<last8>` of the virtual key, or `airlock_client`). No new identity
  concept is introduced.
- **CC-2 Config mechanism.** Per-client and tuning knobs are read **once at
  startup** (consistent with the rest of Airlock). Primary source: a new
  `airlock_settings.circuit_breaker` block in `config.yaml`; override via an
  `AIRLOCK_BREAKER_OVERRIDES` JSON env var, mirroring the existing
  `AIRLOCK_PROVIDER_BUDGETS` / `AIRLOCK_COST_TIERS` pattern. Precedence:
  **per-client override → global default → hard-coded constant**.
- **CC-3 No behavior change without config.** Every new knob defaults to
  today's value (300 s cooldown, threshold 1, escalation 2). A deploy that adds
  no config behaves exactly as it does now. This keeps the pack safe to ship
  incrementally.
- **CC-4 Backwards-compatible errors.** The client-facing error stays
  OpenAI-shaped (`{"error": {message,type,param,code}}`, `docs.py:114-133`); we
  *enrich* it (new `type`, `Retry-After` header, extra fields) without breaking
  existing parsers.
- **CC-5 Observe before enforce.** New limits/visibility ship in an observe/log
  posture first (matching the response-scanner convention) so we can confirm
  against real traffic before changing rejection behavior.

## 4. Recommended sequencing

1. **B + E** first (highest leverage, lowest risk, same `guardian`/`state`
   path): stop this client's bleeding (per-key looser breaker) and make *every*
   client back off correctly (`Retry-After` + typed error). These two would have
   prevented the 46→5 amplification on their own.
2. **A1** alongside (same files): one-strike→threshold + the no-re-arm
   regression test.
3. **C** next: capture quota headroom so 429s stop being surprises.
4. **A2 + A3**: routing fan-out + budget-cap visibility (broader blast-radius
   hygiene; less urgent for this specific client because it pins models).

## 5. Documentation updates required (master list)

Each detail doc repeats its own slice; consolidated here for tracking:

| Doc file | E | B | C | A1 | A2 | A3 |
|----------|---|---|---|----|----|----|
| `docs/getting-started/configuration.md` (env-var table + config.yaml examples) | ✎ | ✎ | ✎ | ✎ | ✎ | ✎ |
| `docs/guide/routing.md` (circuit breaker, fallbacks, budgets) | ✎ | ✎ |  | ✎ | ✎ | ✎ |
| `docs/guide/rate-limiting.md` (**new**: 429 format + Retry-After + per-client breaker) | ✎ | ✎ |  | ✎ |  |  |
| `docs/guide/provider-observability.md` (**new**: quota headroom) |  |  | ✎ |  |  | ✎ |
| `docs/operations.md` (metrics, callbacks, `/health/circuits`) | ✎ | ✎ | ✎ | ✎ |  | ✎ |
| `docs/guide/tui.md` (Guards/Overview screens, new quota surface) | ✎ |  | ✎ |  |  | ✎ |
| `docs/troubleshooting.md` (new diagnoses) | ✎ | ✎ | ✎ | ✎ | ✎ | ✎ |

## 6. Related

- [design-circuit-breaker-per-client.md](design-circuit-breaker-per-client.md) — A1 + E
- [design-rate-limit-client-errors.md](design-rate-limit-client-errors.md) — B
- [design-provider-quota-observability.md](design-provider-quota-observability.md) — C
- [design-routing-fanout-guardrails.md](design-routing-fanout-guardrails.md) — A2 + A3
- Batch gateway (operator-owned mitigation, out of scope here):
  `docs/guide/batch.md`, `dev/notes/handoff-fathom-vllm-batch-throughput.md`
</content>
</invoke>
