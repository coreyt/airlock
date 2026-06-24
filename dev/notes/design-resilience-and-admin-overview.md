# Design: Resilience + Admin/Auth — unified review & reconciliation

**Date:** 2026-06-23
**Status:** Implemented in 0.5.0 (branch `feat/0.5.0-resilience-admin`; see `dev/plans/runs/STATUS-0.5.0.md`). Umbrella review over SIX design
notes; defines the cross-cutting decisions and pack sequencing that let them be
built without conflicting.
**Scope:** the union of the six notes below — `airlock/fast/{guardian,monitor,state}.py`,
`airlock/proxy.py`, `airlock/callbacks/*`, a new `airlock/admin/` package,
`airlock/guardrails/*`, `airlock/tui/*`, `config.yaml`, docs.
**Audience:** the orchestrator sequencing the 0.5.0 resilience + admin release.

> This note **does not modify** the resilience index
> ([design-large-context-resilience-overview.md](design-large-context-resilience-overview.md));
> it sits above it and reconciles it with the admin/auth/TLS/guardrail-skip design.

---

## 1. Why a unified review

Six design notes were authored independently:

| Note | Workstreams | Core decision |
|---|---|---|
| [design-large-context-resilience-overview.md](design-large-context-resilience-overview.md) | index (A1,A2,A3,B,C,E) | root-cause table + CC-1…CC-5 + sequencing |
| [design-circuit-breaker-per-client.md](design-circuit-breaker-per-client.md) | A1 + E | one-strike → threshold; per-client `BreakerPolicy`; lock no-re-arm |
| [design-rate-limit-client-errors.md](design-rate-limit-client-errors.md) | B | `AirlockProviderBlocked(RateLimitError)` + exception handler + `Retry-After` |
| [design-provider-quota-observability.md](design-provider-quota-observability.md) | C | capture `x-ratelimit-*`; `ProviderRateLimitState`; observe-only |
| [design-routing-fanout-guardrails.md](design-routing-fanout-guardrails.md) | A2 + A3 | suppress fallbacks for large/quarantined; budget warn-at-80% |
| [design-admin-api-capability-auth.md](design-admin-api-capability-auth.md) | Admin/TLS/Skip | admin API; loopback+JWT auth; native TLS; per-request guardrail skip |

They **share four code seams**: `state.py` quarantine/circuit state, the
`guardian.py` pre-call hook, the `install_*_on_proxy_app()` mount point
(`model_override_headers.py:57-60`), and the JSONL record/ingest path
(`enterprise_logger.py` ↔ `state.py:706` `ingest_jsonl_record`). Built naively
they collide — the headline case: the admin **clear-quarantine** lowers
`quarantine_until`, but A1/E's new threshold counter still holds the pre-clear
429s in its window, so the next request **re-arms instantly**. CC-6…CC-12 below
resolve every such collision; §3 fixes the mount order; §4 sequences the packs.

CC-1…CC-5 (client identity, startup-config, no-behavior-change-without-config,
backwards-compatible errors, observe-before-enforce) are defined in the
resilience index §3 and **apply unchanged** to the admin work.

---

## 2. New cross-cutting decisions (CC-6 … CC-12)

### CC-6 — Quarantine clear and the breaker threshold share one "cleared floor"
A1/E rewrites arming to gate on `recent_rate_limit_count(window) >=
policy.rate_limit_threshold` (`state.py:205` / `:179`). If admin clear only
lowers `quarantine_until`, the deque still holds pre-clear 429s inside the window
→ instant re-arm. **A1/E adds `cleared_at: float = 0.0`** to both
`ClientProviderState` and `ProviderState`. **The floor applies to *every* reader
of the rate-limit history that can re-arm a breaker**, not just the client
threshold:
- `ClientProviderState.recent_rate_limit_count(window)` counts only events with
  `t > max(now - window, cleared_at)` (client→provider threshold).
- **`ProviderState.impacted_clients()` (`state.py:287`, used by provider-wide
  escalation at `state.py:618`) must apply the same `cleared_at` floor** — else a
  provider clear is undone by pre-clear *client* history on the next 429
  (codex BLOCK, 2026-06-23). Both the provider's own `cleared_at` and each
  client→provider bucket's `cleared_at` floor here (whichever is later per
  bucket).

History is preserved for logging; pre-clear events are hidden from all arming
logic. **Admin clear sets `cleared_at = now`** on the affected bucket(s). A1/E
**owns** the fields and the floored readers; admin **only writes** `cleared_at`.

### CC-7 — Half-open is an explicit probe gate on the provider/client breaker
Today only `ModelState` has a circuit (`state.py:430/435`); `ProviderState` /
`ClientProviderState` have none. A1/E adds **`_half_open_probe: bool = False`** to
both, default off (CC-3). `mode=probe` sets it; a success closes (clears
`quarantine_until`), a failure re-arms using
`BreakerPolicy.{client,provider}_cooldown_seconds`. Shipped in the breaker pack so
admin builds against a stable shape.

### CC-8 — Exact `StateStore` mutator contract (breaker ships fields, admin ships methods)
```
clear_provider_quarantine(provider, *, mode, actor, now) -> dict          # mode: "probe"|"force"
clear_client_provider_quarantine(client_id, provider, *, mode, actor, now) -> dict
clear_client_backoff(client_id, *, actor, now) -> dict
reset_model_circuit(model, *, actor, now) -> dict
quarantine_provider(provider, *, actor, now, cooldown) -> dict            # manual arm, loopback-only
```

**Why both a provider clear and a client→provider clear (codex BLOCK,
2026-06-23):** a pinned request checks the **client→provider** bucket
(`ClientProviderState.quarantine_until`) *before* provider-wide state
(`guardian.py:224` precedes `:254`). The incident that motivates UN-10 is a
*single client's* per-client quarantine. So:
- `clear_client_provider_quarantine(client_id, provider, …)` clears exactly that
  victim bucket — the precise UN-10 operation.
- `clear_provider_quarantine(provider, …)` clears the provider-wide state **and
  cascades** to every `(client, provider)` bucket for that provider (an operator
  clearing "openai" means "unblock everyone on openai"); without the cascade the
  provider reads clear but pinned clients stay blocked.

**Per-mutator side effects (the `cleared_at`/half-open semantics apply only to
the quarantine-clearing mutators):**
| mutator | target state | sets `cleared_at` | half-open (`_half_open_probe`) |
|---|---|---|---|
| `clear_client_provider_quarantine` | `ClientProviderState` (one bucket) | ✓ | ✓ on `probe` |
| `clear_provider_quarantine` | `ProviderState` + all its `ClientProviderState` buckets | ✓ (each) | ✓ on `probe` (provider + buckets) |
| `clear_client_backoff` | `ClientState.backoff_until` (`state.py:60`) | — (no breaker history) | — |
| `reset_model_circuit` | `ModelState` (`state.py:421`, reuse its half-open) | — | reuses `ModelState` circuit |
| `quarantine_provider` | `ProviderState` (manual arm) | — | — |

All run under the existing `RLock`. For `mode="probe"` set `quarantine_until =
now` + `_half_open_probe = True`; for `"force"` set `quarantine_until = 0.0`.
Respect the resolved `BreakerPolicy` (`disabled` → no-op; `escalation_exempt`
clients excluded from any re-escalation). Each **returns the `admin_action`
payload that becomes the JSONL record** — mutation, audit, and TUI-replication are
one object.

### CC-9 — A `record_type` discriminator unifies C's enrichment and the admin_action record
`ingest_jsonl_record` early-returns when `record["model"]` is absent
(`state.py:715`) → an `admin_action` record (no model) would be **silently
dropped**. Add **`record_type`** in `_build_record` (`enterprise_logger.py:470`):
request records → `"request"` (treated as the default when the key is absent, for
back-compat); admin mutators → `"admin_action"`. `ingest_jsonl_record` branches on
`record_type` **before** the model check (`"request"` → existing path;
`"admin_action"` → `_ingest_admin_action` calling the **same CC-8 mutator** so the
TUI replica converges). C's `provider_ratelimit` is an additive field on the
request record — no collision. **Ownership: C introduces `record_type` (it already
edits both `_build_record` and ingest); admin adds only the `"admin_action"`
branch.** ⇒ sequence C before admin-state.

### CC-10 — GuardrailDecision governs CONTENT guards only, never the breaker or fallbacks
The skip resolver, the breaker (under the `if not mcp and not batch` gate,
`guardian.py:201`), and A2's `_suppress_fallbacks` all live in guardian pre-call,
but read **different** fields:
- Resolver output = (i) request-class {interactive,batch,mcp} and (ii) per-guardrail
  effective modes {pii_redact, keyword, response_scan, reasoning_strip}.
- Breaker and A2 fallback-suppression read **only the request-class**. A capability
  skip may downgrade `keyword`→observe but can **never** disable the breaker
  (`BreakerPolicy.disabled` is operator-config) or re-enable fallbacks
  (`disable_fallbacks` is A2/operator-config). **Provider-protection is
  non-skippable.**
- The existing `batch`/`mcp` booleans (`guardian.py:169-172`, `monitor.py:187`)
  become resolver inputs; content hooks read their entry from the stamped decision
  instead of env.

**Where the resolver runs — and the ASGI→LiteLLM handoff (codex CONCERN,
2026-06-23).** The content guards already receive LiteLLM `data` (incl.
`data["metadata"]`) in their hooks — `keyword_guard.py:78` today reads the env
flag and `pii_guard.py:202` today writes `airlock_pii_map` metadata; **this pack
modifies each hook to also read its mode from
`data["metadata"]["airlock_guardrail_decision"]`** (those line refs are the
edit sites, not existing decision-readers). The `GuardrailDecision` must therefore
land in `data["metadata"]`. The ASGI **perimeter middleware does NOT rewrite the
request body** for
guardrail skips — it owns only `/airlock/admin/*` routing + admin auth. Instead:
- **The skip resolver runs inside the guardian pre-call hook**, which already has
  `data` (and the proxy request headers via LiteLLM's
  `data["metadata"]["headers"]` / `proxy_server_request`, the same place
  `client_id` is derived). It reads + verifies the `X-Airlock-Capability` token
  there, builds the `GuardrailDecision`, and stamps it into
  `data["metadata"]["airlock_guardrail_decision"]`.
- Content hooks read that key from `data["metadata"]`. No ASGI body rewrite, no
  cross-layer handoff. (If a future need forces resolution earlier than the
  guardian hook, the perimeter sets `request.state` and a minimal pre-call bridge
  copies it into `data["metadata"]` — but the default is resolve-in-guardian.)
- The shared token verify/scope logic lives in `airlock/admin/tokens.py`; only the
  *call site* differs between admin routes (perimeter) and skips (guardian hook).

Pre-call order: guardian resolves the decision (capability verify) + reads
request-class for the breaker gate → A2 `_suppress_fallbacks` → content hooks
consult `data["metadata"]["airlock_guardrail_decision"]`.

### CC-11 — Capability `sub` binds to the AUTHENTICATED identity, not a forgeable header
The JWT `sub` is a `client_id` of the form `key:<last8>` (CC-1). **Critical
security correction (codex BLOCK, 2026-06-23):** `client_id` today has *two*
sources — the authenticated `key:<last8>` derived from the validated
`Authorization` bearer key, **and** the unauthenticated `airlock_client` /
`X-Airlock-Client` attribution header (`client_identity.py:33`;
`guardian._request_client_id:66` / `monitor._extract_client_id:41` prefer the
header). Binding a guardrail-skip token to "the resolved client_id" is therefore
**insufficient**: an attacker who steals a skip token can forge
`X-Airlock-Client: <sub>` and replay it.

Resolution: **for `guardrail:skip:*` authorization the PDP compares `token.sub`
only to the `key:<last8>` derived from the request's *validated bearer key*** —
never to the `X-Airlock-Client` header. A request with no authenticated key (or
whose key-derived id ≠ `sub`) gets no skip, regardless of any
`X-Airlock-Client` value. The forgeable attribution header keeps its
logging/attribution role but carries **zero** authorization weight for skips.

For `admin:*`, `sub` is the audit actor only (the target is in the URL); admin
authorization comes from the JWT signature + loopback (Path A/B), not from `sub`.
`policy_for(<key-derived id>)` and the skip resolver thus refer to the same
`BreakerPolicy`.

### CC-12 — All new config loads once at startup, off by default — mind the process split
Subprocess (litellm callbacks) loads `airlock_settings.{circuit_breaker,fallbacks,budgets}`,
`admin:`, and `guardrail_overrides:` into the `store` singleton; env overrides
mirror `AIRLOCK_PROVIDER_BUDGETS`. **The parent process reads `AIRLOCK_SSL_CERTFILE`/
`AIRLOCK_SSL_KEYFILE` in the `litellm_cmd` builder (`proxy.py:374`)** — TLS is a
uvicorn flag, not a subprocess setting. The TLS pack and the breaker pack must not
assume a shared loader. Defaults (CC-3): `admin.enabled:false` → `/airlock/admin/*`
= 404 + capability header ignored; `allow_capability_skip:false`; breaker threshold
1 / 300 s / escalation 2. A config-free deploy behaves exactly as today.

**Fail-closed on insecure token auth (codex CONCERN, 2026-06-23).** Bearer tokens
(admin or capability) over plaintext on an exposed bind are replayable. So at
startup, if (`admin.enabled` **or** `allow_capability_skip`) **and** the bind is
non-loopback (`AIRLOCK_HOST` ∉ {127.0.0.1, ::1, localhost}) **and** native TLS is
off (`AIRLOCK_SSL_*` unset), the proxy **refuses to start** unless the operator
sets `admin.allow_insecure_tokens: true` (which downgrades to a loud warning).
Loopback-only deploys and TLS-terminating front-proxy deploys are unaffected
(set `admin.behind_tls_proxy: true` to assert the latter).

---

## 3. Mounting order on the proxy app

Import-time installs run in the litellm **subprocess** (`model_override_headers.py:57-60`).
Required ASGI layering, outermost → innermost:

1. **BatchGatewayMiddleware** — outermost; dispatches batch before any auth
   (`batch/middleware.py:158`).
2. **Admin perimeter middleware** — gates `/airlock/admin/*` and authenticates
   admin requests (loopback / admin JWT). It does **not** handle the
   `X-Airlock-Capability` guardrail-skip token — that is verified + stamped in the
   guardian pre-call hook (CC-10).
3. **LiteLLM auth + routes** — innermost; guardian pre-call raises here.
4. **B's exception handler** — terminal (`add_exception_handler`, order-independent).

Starlette `add_middleware` inserts at index 0 (most-recent = outermost). To keep
the batch gateway outermost, **`install_admin_on_proxy_app()` must be called
between `model_override_headers.py:59` and `:60`** — before the gateway install —
on both the pre-start `add_middleware` path and the post-start stack-wrap path
(`batch/middleware.py:576-583`). Use an `app.state.airlock_admin_installed`
idempotency guard mirroring `airlock_batch_gateway_installed`.

**Conflict to prevent:** the perimeter middleware must return its **own**
401/403/404 and **never raise `RateLimitError`** — otherwise B's handler would
mis-shape an auth failure as a provider-429 body.

---

## 4. Pack sequencing (DAG)

The resilience index order (B+E → A1 → C → A2+A3) is preserved; admin packs
interleave by hard data dependency.

| Pack id | Content | Depends on | Parallelism |
|---|---|---|---|
| `0.5.0-RES-tls` | `proxy.py:374` env→ssl flags | — (parent-only) | ∥ everything; land before admin GA (TLS protects JWTs) |
| `0.5.0-RES-breaker` (E+A1) | `state.py` threshold + `BreakerPolicy` + **`cleared_at` (CC-6)** + **`_half_open_probe` (CC-7)** + no-re-arm test | — | **fixes final `state.py` shape** |
| `0.5.0-RES-errors` (B) | `proxy_errors.py` + handler install + typed raise (`guardian.py:105-128`) | breaker | overlaps observ (disjoint files) |
| `0.5.0-RES-observ` (C) | monitor/state/logger/metrics/TUI + **`record_type` (CC-9)** | breaker | overlaps errors |
| `0.5.0-RES-routing` (A2+A3) | guardian `_suppress_fallbacks` + budget warn | breaker, errors, observ | — |
| `0.5.0-ADM-state` | CC-8 mutators + `admin_action` + `_ingest_admin_action` | **breaker + observ** | no HTTP |
| `0.5.0-ADM-jwt` | `tokens.py` + `airlock admin mint-token` | config only | ∥ all of the above |
| `0.5.0-ADM-http` | PDP + perimeter middleware + `/airlock/admin/*` (§3) | ADM-state, ADM-jwt, errors | — |
| `0.5.0-ADM-tui` | keybindings via the screen `BINDINGS` + action handlers (the cooldown the action targets is rendered at `overview.py:603`) → loopback client (skip-verify 127.0.0.1) | ADM-http | ∥ skip |
| `0.5.0-ADM-skip` | resolver + `X-Airlock-Capability`; folds batch/mcp (CC-10) | ADM-http, ADM-jwt | ∥ tui |

**Critical path:** `RES-breaker → {RES-observ → ADM-state} → ADM-http → {ADM-tui, ADM-skip}`.
**Parallel:** `RES-tls` ∥ all; `ADM-jwt` ∥ RES packs; `RES-errors` ∥ `RES-observ`
once breaker lands (disjoint files).

---

## 5. Conflict-risk register

| # | Risk if built independently | Neutralized by |
|---|---|---|
| R1 | Admin clear → instant re-arm vs A1 threshold counter | CC-6 `cleared_at` (breaker owns, admin writes) |
| R2 | No HALF_OPEN on provider/client breaker (only `ModelState`) | CC-7 `_half_open_probe` in breaker pack |
| R3 | `ingest_jsonl_record` drops `admin_action` (no `model`, `state.py:715`) | CC-9 `record_type` branch before model check |
| R4 | C and admin both edit `_build_record`/ingest | CC-9: C introduces `record_type`; admin adds only its branch; C first |
| R5 | GuardrailDecision disables breaker/fallbacks | CC-10 field separation; provider-protection non-skippable |
| R6 | Token grants skip to wrong identity | CC-11 PDP binds skip to the **authenticated key-derived** id, never `X-Airlock-Client` |
| R7 | Perimeter mounts wrong vs gateway / B handler | §3 install order; perimeter never raises `RateLimitError` |
| R8 | Two packs rewriting `state.py` (breaker vs admin) | ADM-state starts after RES-breaker final; breaker reserves the fields |
| R9 | `disabled` policy vs admin clear semantics | Document: force overrides wall-clock, but a `disabled` client still won't 429-re-arm; manual arm is provider-scoped |
| R10 | Native-TLS self-signed cert breaks loopback TUI client | Skip-verify for `127.0.0.1` only in ADM-tui |
| R11 | TLS loads in parent, rest in subprocess | CC-12: keep TLS in `proxy.py:374`; everything else in the store injector |
| R12 | Provider clear leaves the per-client victim quarantined (pinned check is client→provider first, `guardian.py:224`) | CC-8 client→provider clear + provider-clear cascade |
| R13 | Provider clear re-arms via `impacted_clients()` on pre-clear history | CC-6 floor extended to `impacted_clients()` (`state.py:287/618`) |
| R14 | Skip token replayed by forging `X-Airlock-Client` | CC-11 authenticated-identity binding |
| R15 | Tokens replayable over plaintext on an exposed bind | CC-12 fail-closed: refuse start unless TLS / loopback / `allow_insecure_tokens` |
| R16 | `GuardrailDecision` unreachable from ASGI perimeter to LiteLLM hooks | CC-10 resolve-in-guardian-hook into `data["metadata"]`; no body rewrite |

> **Design-review round 1 (codex `gpt-5.5`, 2026-06-23 — `0.5.0-RESADMIN-design-review-20260623T170552Z.md`):**
> verdict BLOCK → resolved. 3 BLOCKs (R12 client→provider clear, R14 forgeable-identity
> binding, R13 escalation floor) + 5 CONCERNs (R15 fail-closed TLS, R16 resolver
> handoff, per-mutator `cleared_at` scope, `overview.py:603` anchor, Docker
> `/health`→`/health/liveliness`) folded into CC-6/CC-8/CC-10/CC-11/CC-12 and the
> detail notes. codex confirmed CC-7, CC-9 (`state.py:714` drop), the mount order,
> CC-12, and the UN trace as accurate. Re-review pending.
>
> **Round 2 (`0.5.0-RESADMIN-design-review-r2.md`):** verdict BLOCK — the umbrella
> was confirmed *correct*, but three fixes hadn't propagated to satellite docs/code:
> (1) the `/health` liveness violation was **repo-wide** (`docker-compose.yml`,
> `deploy/k8s/deployment.yaml`, `agents/config-deployment.md`), now all on
> `/health/liveliness`; (2) CC-11 stale in `architecture.md`/`user-needs.md` + mint
> examples used non-key `sub`, now key-derived; (3) CC-10 perimeter-vs-guardian
> placement stale in the admin note + `architecture.md`, now resolve-in-guardian
> everywhere. CONCERN (guard anchors are edit sites, not current readers) reworded.
> Re-review pending.
>
> **Round 3 (`…-r3.md`):** verdict BLOCK — named files confirmed fixed + full
> regression pass, but two propagation tails: (1) more `/health` probe *docs*
> (`docs/operations.md`, `dev/requirements.md`, `docs/troubleshooting.md`, plus
> swept `dev/tui-design.md` 5 s poll + `agents/config-deployment.md` advisory) →
> all `/health/liveliness`; (2) two more perimeter-stamps-decision mentions
> (umbrella §3 layer-2, admin §2/§4.2 PEP + verify) → corrected to
> admin-routes-only / verify-in-guardian. `airlock status` (CLI status, not a
> probe) and the "don't probe /health" warnings left as-is. Re-review pending.
>
> **Round 4 (`…-r4.md`):** verdict BLOCK — two last mentions: `docs/operations.md:32`
> ("reserve `/health` for readiness-style checks" → liveliness handles readiness
> too) and the admin §5.4 client-path line ("Middleware verifies the token" →
> guardian pre-call hook). Both fixed; a full repo sweep confirms no remaining
> automated-probe `/health` or perimeter-verifies-capability text.
>
> **Round 5 (`…-r5.md`): verdict PASS — gate cleared.** No BLOCK, no CONCERN.
> codex re-verified CC-6…CC-12, mount order, the UN-10…18 trace, and the `/health`
> + perimeter/guardian consistency across docs, manifests, AND real code
> (`airlock/tui/screens/overview.py:364`, `airlock/cli/status_cmd.py:27`,
> `airlock/hooks/_common.py` already use `/health/liveliness`). **Design gate
> closed; implementation (Phase E) may proceed.**

---

## 6. Documentation updates (delta over the resilience index §5)

The resilience index §5 matrix covers E/B/C/A1/A2/A3. The admin work adds:

| Doc file | TLS | Admin | Skip |
|---|---|---|---|
| `docs/getting-started/configuration.md` (`admin:`, `guardrail_overrides:`, `AIRLOCK_SSL_*`, `AIRLOCK_JWT_SECRET`) | ✎ | ✎ | ✎ |
| `docs/operations.md` (admin API, loopback/JWT auth, native TLS, mint-token, audit) | ✎ | ✎ | ✎ |
| `docs/guide/tui.md` (clear-quarantine keybindings) |  | ✎ |  |
| `docs/guide/guardrails.md` (per-request skip, non-skippable PII) |  |  | ✎ |
| `docs/troubleshooting.md` ("stale quarantine — clear it"; "admin 404 — enable it") |  | ✎ |  |
| `docs/guide/admin-api.md` (**new**: endpoints, auth, capability tokens) |  | ✎ | ✎ |

---

## 7. Process gates

1. **Requirements** — UN-10…UN-18 in `dev/user-needs.md` (admin/auth/TLS/skip +
   resilience backfill); each traces to a design note.
2. **Architecture** — `dev/architecture.md` §3.6 + §9 + §8 additions.
3. **Design-time codex review (blocks implementation)** — `codex exec` over this
   umbrella + the six notes + the architecture additions →
   `dev/plans/runs/0.5.0-RESADMIN-design-review-<ts>.md`; require **PASS** before
   any pack is implemented.
4. **Per-pack** — TDD (no-network unit tests, `uv run python -m pytest -q`) →
   per-pack codex review → STATUS update → docs from §6.

## 8. Related

- Resilience index + four detail notes (see §1 table).
- [design-admin-api-capability-auth.md](design-admin-api-capability-auth.md).
- Release spine: `dev/plans/0.5.0-plan.md`, `dev/plans/runs/STATUS-0.5.0.md`.
- Requirements: `dev/user-needs.md` UN-10…UN-18.
