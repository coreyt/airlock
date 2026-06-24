# Design: Descriptive client-facing rate-limit errors + Retry-After (B)

**Date:** 2026-06-23
**Status:** Design proposal (pre-implementation).
**Scope:** new `airlock/proxy_errors.py` (FastAPI exception handler) +
registration seam in `airlock/callbacks/model_override_headers.py`,
`airlock/fast/guardian.py`, `airlock/docs.py`.
**Index:** [design-large-context-resilience-overview.md](design-large-context-resilience-overview.md)
(cross-cutting CC-1…CC-5).

---

## 1. Summary

When the breaker quarantines a client, `guardian._raise_provider_protection`
raises a **generic `litellm.RateLimitError`** (`guardian.py:124-128`). The rich
context — that it's *Airlock's* breaker (not the provider), the cooldown
seconds, the scope, the upstream reason — is written to
`metadata["airlock_provider_protection"]` (`monitor.py:220-233`) but **never
reaches the client**. There is **no `Retry-After` header**.

Consequence, observed in the logs: the client retries the large call **every
1–9 seconds** for the whole 300 s window because nothing tells it to back off.
One real 429 → ~46 wasted quarantine-blocked retries (overview §1).

**Confirmed plumbing constraint (Explore agent):** post-call hooks
(`async_post_call_response_headers_hook`) only fire on **successful** responses;
a raised exception bypasses them entirely. So headers/body on an *error* cannot
be set from a callback — they require a **FastAPI exception handler registered
on the proxy app**.

## 2. Hard design decisions

1. **Custom exception handler on the proxy app.** Add an
   `install_airlock_error_handlers_on_proxy_app()` that registers a handler for
   `litellm.exceptions.RateLimitError` (and the Airlock-block subtype below),
   following the existing `install_*_on_proxy_app()` pattern invoked at
   `model_override_headers.py:57-60`. This is the only place that can shape the
   error status/headers/body.
2. **Typed subclass for Airlock blocks.** Introduce
   `AirlockProviderBlocked(RateLimitError)` raised by
   `_raise_provider_protection`, carrying structured fields
   (`cooldown_seconds`, `scope`, `provider`, `reason`, `client_id`). This lets
   the handler distinguish *Airlock breaker* from *passthrough provider 429*
   without string-parsing.
3. **`Retry-After` header, always.** For any rate-limit response, emit
   `Retry-After: <ceil(cooldown_seconds)>`. For Airlock blocks the value is the
   breaker cooldown (from workstream E's policy); for passthrough provider 429s,
   the provider's own `Retry-After` / `x-ratelimit-reset-*` if present
   (available via `exc.response.headers`, see C), else a sane default.
4. **Enriched but compatible body (CC-4).** Keep OpenAI shape
   (`docs.py:114-133`); add a stable `type` and an `airlock` sub-object:

   ```json
   {
     "error": {
       "message": "Airlock paused requests to openai for this client to protect upstream standing. Retry after 30s.",
       "type": "airlock_circuit_breaker",      // vs "provider_rate_limit"
       "code": "provider_blocked",
       "param": null,
       "airlock": {
         "scope": "client_provider",            // or "provider"
         "provider": "openai",
         "cooldown_seconds": 30,
         "retry_after": 30,
         "reason": "litellm.RateLimitError: ...quota...",
         "source": "circuit_breaker"            // vs "provider"
       }
     }
   }
   ```
5. **Status code 429** for both cases. Add `X-Airlock-Provider-State:
   quarantined` / `X-Airlock-Block-Scope` headers for quick client/operator
   triage (consistent with existing `X-Airlock-*` headers).
6. **No secrets in `reason`.** The upstream reason is already a sanitized error
   string; the handler truncates and strips any key-like tokens defensively.

## 3. Wiring

- **New `airlock/proxy_errors.py`:**
  - `class AirlockProviderBlocked(RateLimitError)` with structured attrs.
  - `async def _airlock_rate_limit_handler(request, exc)` → builds the JSON body
    + `Retry-After` + `X-Airlock-*` headers; branches on
    `isinstance(exc, AirlockProviderBlocked)` vs plain `RateLimitError`
    (passthrough provider 429 — pull `exc.response.headers` for reset/retry).
  - `def install_airlock_error_handlers_on_proxy_app() -> None`: resolve the
    litellm proxy FastAPI `app` (same accessor the other `install_*` helpers
    use) and `app.add_exception_handler(...)`.
- **`model_override_headers.py:57-60`:** add
  `install_airlock_error_handlers_on_proxy_app()` next to the existing installs.
- **`guardian.py:105-128`:** `_raise_provider_protection` raises
  `AirlockProviderBlocked(...)` (still a `RateLimitError`, so existing
  `except RateLimitError` paths and `monitor` rate-limit detection are
  unaffected) and passes the structured fields it already computes.
- **`docs.py`:** extend the documented error schema (114-133) with the new
  `type` values and the `airlock` sub-object so the OpenAPI surface is accurate.

## 4. Edge cases

- **Passthrough provider 429** (provider returns 429 and Airlock does *not*
  block): handler still runs (matches `RateLimitError`), sets
  `type="provider_rate_limit"`, `source="provider"`, and a `Retry-After` from
  `exc.response.headers` when available. This gives clients backoff signal even
  before the breaker arms.
- **Streaming requests:** if the block happens pre-call (it does — guardian is a
  pre-call hook), the stream never starts, so the JSON error + status is correct
  (no partial SSE). Verify no SSE framing is forced for pre-call raises.
- **Non-rate-limit errors:** handler is scoped to `RateLimitError` only;
  everything else keeps litellm's default handling.

## 5. Tests (TDD, RED first)

`tests/test_proxy_errors.py` (handler unit, no network):
- `AirlockProviderBlocked` → 429, body `type=airlock_circuit_breaker`,
  `Retry-After` == ceil(cooldown), `airlock.scope/provider/cooldown_seconds`
  populated.
- plain `RateLimitError` with `exc.response.headers` carrying
  `x-ratelimit-reset-requests` → `Retry-After` derived from it,
  `type=provider_rate_limit`.
- plain `RateLimitError` with no headers → default `Retry-After`.
- body stays OpenAI-compatible (top-level `error.message/type/code/param`).

`tests/test_fast_guardian.py`:
- quarantined client → `AirlockProviderBlocked` raised with correct fields;
  `isinstance(exc, RateLimitError)` still true (compat).

Integration (proxy app TestClient): a quarantined request returns 429 +
`Retry-After` + the structured body end-to-end.

## 6. Documentation updates

- `docs/guide/rate-limiting.md` (**new**, shared with E): the 429 contract —
  status, `Retry-After`, body schema, `type` values, recommended client backoff
  (honor `Retry-After`; do not tight-loop). Include a "what your client should
  do" snippet.
- `docs/getting-started/configuration.md`: note the error shape / headers.
- `docs/operations.md`: the new exception handler in the callback/boot section;
  `X-Airlock-Provider-State` / `X-Airlock-Block-Scope` headers.
- `docs/troubleshooting.md`: "Client sees empty responses under load" → it's a
  429; have the client honor `Retry-After`.
- `docs/index.md`: link the new rate-limiting guide.

## 7. Out of scope

- Changing *when* we block (that's E/A1).
- A streaming "error frame" inside an already-open SSE stream (blocks are
  pre-call; not needed now).

## 8. Related

Cooldown value comes from [E](design-circuit-breaker-per-client.md); provider
`Retry-After`/reset extraction shares the header-capture work in
[C](design-provider-quota-observability.md).
</content>
