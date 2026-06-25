# Design: Admin API + capability auth (native TLS, loopback ops, JWT skips)

**Date:** 2026-06-23
**Status:** Implemented in 0.5.0 (branch `feat/0.5.0-resilience-admin`; see `dev/plans/runs/STATUS-0.5.0.md`).
**Scope:** `airlock/proxy.py`, `airlock/fast/state.py`, a new `airlock/admin/`
package, `airlock/guardrails/*`, `airlock/tui/screens/overview.py`,
`airlock/cli/main.py`, `config.yaml`, docs.
**Related:**
[design-rate-limit-client-errors.md](design-rate-limit-client-errors.md),
[design-circuit-breaker-per-client.md](design-circuit-breaker-per-client.md)
(this note gives operators a way to *clear* the quarantine those notes arm),
[design-provider-quota-observability.md](design-provider-quota-observability.md).
**Reconciliation (read first):**
[design-resilience-and-admin-overview.md](design-resilience-and-admin-overview.md)
governs how this design composes with the five resilience notes —
**CC-6** (`cleared_at` floor, owned by the breaker pack; this pack only writes it),
**CC-7** (`_half_open_probe`, owned by the breaker pack), **CC-8** (the exact
`StateStore` mutator contract this pack implements), **CC-9** (`record_type`,
introduced by the observability pack; this pack adds the `admin_action` branch),
**CC-10** (the `GuardrailDecision` governs content guards only — never the breaker
or fallbacks), **CC-11** (`sub` == `client_id`), **CC-12** (startup-config + the
parent/subprocess TLS split). Where this note and the umbrella differ, the
umbrella wins.

---

## 1. Summary — the two needs

1. **An admin API** to mutate live protection state — primarily *clear/accelerate
   a provider quarantine* after a verified credit top-up, plus reset a model
   circuit and clear a client backoff. Today there is **no clear path**: once
   `quarantine_until` is set it only drains by wall-clock (`state.py` sets it via
   `max(quarantine_until, now + 300)` at `:188`/`:263`; nothing lowers it).
   `GET /health/circuits` is the only state endpoint and it is read-only.
2. **Per-request guardrail skips** for trusted clients (interactive now; batch
   when that guardrail path lands), so a benchmark client can run with, e.g., the
   keyword guard downgraded — **without** a global env flip.

Both must be reachable **(A)** by a local operator via the TUI and **(B)**
remotely/programmatically, gated by config and off by default.

### Two grounding facts that shape everything

- **The TUI is a separate process.** It tails JSONL (`tui/app.py:165`) and
  rebuilds its *own* `StateStore` via `ingest_jsonl_record` (`state.py:706`). It
  has **no in-process access** to the proxy's live state. ⇒ A TUI action cannot
  mutate state directly; it must call an HTTP admin endpoint on the proxy. **The
  admin API is the foundation; the TUI is just a client of it.**
- **Routes mount via the callback-import trick.** `install_*_on_proxy_app()`
  already adds `GET /health/circuits` and the batch gateway
  (`callbacks/model_override_headers.py:55-60`, `batch/middleware.py:546-583`,
  with the pre-start `add_middleware` / post-start stack-wrap dual path). New
  admin routes + a perimeter middleware mount the **same** way.

State today: bind defaults to **loopback** `127.0.0.1` (`proxy.py:350`, with an
explicit "set `AIRLOCK_HOST=0.0.0.0` to expose" comment); auth is a **single
master key** (`AIRLOCK_MASTER_KEY`, constant-time Bearer compare,
`batch/middleware.py:158-173`); `X-Airlock-Client` is an **unauthenticated,
forgeable claim** used only for attribution (`client_identity.py:33`); **no DB,
no JWT/OIDC** configured.

---

## 2. Hard design decisions

1. **Admin API in the proxy process; TUI is an HTTP client of it** (forced by the
   process boundary in §1). Mutations emit a JSONL `admin_action` record that is
   simultaneously the **audit log**, the **TUI-replica update** (via
   `ingest_jsonl_record`), and **crash recovery**. The audit trail *is* the
   state-propagation channel — design the mutation and its record as one thing.
2. **Native TLS via LiteLLM passthrough** (Part A), in addition to the existing
   reverse-proxy option. Env-driven, default unchanged (HTTP).
3. **Auth = two independent paths, one PDP** (Part B):
   - **Path A — loopback = operator.** Connection from `127.0.0.1`/`::1` ⇒
     operator tier, no credential. Zero infra; this is the TUI path.
   - **Path B — JWT capability token.** Short-lived HS256 token, signed by a
     server-side secret, carrying `{sub, scope[], exp}`. Unforgeable identity +
     scope + expiry. Works remotely. Zero infra (no DB/IdP).
   - Master key remains root: break-glass full admin **and** the credential that
     mints tokens.
4. **One token type for both features.** Admin verbs and guardrail skips are just
   different `scope` strings (`admin:clear_quarantine`,
   `guardrail:skip:keyword`). A capability is a signed list of scopes.
5. **Skip = downgrade, not silence.** A granted skip lowers a guardrail's
   *effective mode* (default `observe`), preserving the scan + audit. `pii_redact`
   is **non-skippable by default** (compliance / exfil risk).
6. **Off by default; non-breaking.** Admin disabled ⇒ `/airlock/admin/*` returns
   `404` (don't confirm existence) and capability headers are ignored. Existing
   clients are untouched.

---

## 3. Part A — native HTTPS

LiteLLM's CLI forwards `--ssl_certfile_path` / `--ssl_keyfile_path` to uvicorn.
Populate them from env in the `litellm_cmd` builder (`proxy.py:374`):

```python
ssl_cert = os.getenv("AIRLOCK_SSL_CERTFILE")
ssl_key  = os.getenv("AIRLOCK_SSL_KEYFILE")
if ssl_cert and ssl_key:
    litellm_cmd += ["--ssl_certfile_path", ssl_cert, "--ssl_keyfile_path", ssl_key]
```

> **Verify flag spelling** against the pinned litellm version — uvicorn-passthrough
> names have drifted across releases.

- Both set ⇒ HTTPS on the same `AIRLOCK_HOST:AIRLOCK_PORT`. Neither ⇒ HTTP, as
  today. Result: HTTP, native HTTPS, or reverse-proxy HTTPS — operator's choice.
- **Certs load at startup only** ⇒ renewal = restart (rolling restart is fine;
  document it). A front proxy is still preferable when you need hot cert rotation,
  an LB, or HTTP→HTTPS redirect.
- mTLS is a clean future upgrade (uvicorn `--ssl_ca_certs` + cert-reqs); not
  required for A+B.
- **Client impact:** scheme only — `base_url` `http://` → `https://`.

---

## 4. Part B — auth model (A loopback + B JWT)

```
                        ┌──────────────── AdminPolicy / PDP ────────────────┐
request → perimeter ───►│  Path A: connection is loopback  ───► operator     │
          middleware    │  Path B: valid JWT (sig+exp+scope) ─► scoped grant │
                        │  Path 0: master key ───────────────► full / mint   │
                        └────────────────────────────────────────────────────┘
```

Admitted if **either** path succeeds. **Two enforcement points share one PDP:**
the **perimeter middleware** authenticates and gates `/airlock/admin/*` (admin
routes only); the **guardian pre-call hook** verifies the `X-Airlock-Capability`
guardrail-skip token and stamps the validated `GuardrailDecision` into
`data["metadata"]` (CC-10). Neither lets a downstream content hook see a raw,
unvalidated token.

### 4.1 Path A — loopback = operator

PEP checks ASGI `scope["client"]`; loopback + `trust_loopback` ⇒ operator tier,
no credential. Rationale: reaching loopback means shell on the host ≈ proxy
privilege.

**Caveat (must document):** a reverse proxy forwarding to airlock on loopback
makes *every* request appear local. Mitigations, per deployment:
1. front proxy **blocks `/airlock/admin/*`** externally (simplest);
2. `trust_loopback: false` when exposed (force Path B even locally);
3. bind admin to a **Unix domain socket** (needs a second listener — an upgrade,
   not free under the single-uvicorn app).

Default `trust_loopback: true`; **warn at startup** if `AIRLOCK_HOST != 127.0.0.1`
and it is still on.

### 4.2 Path B — JWT capability tokens

- **HS256**, signed with `AIRLOCK_JWT_SECRET` (dedicated; HKDF-derive from
  `AIRLOCK_MASTER_KEY` if unset). Symmetric is fine — only airlock verifies and
  only the operator mints; go asymmetric (RS256/EdDSA) only for multi-node
  verify-key distribution.
- **Claims:** `iss:"airlock"`, `sub:"<client-id>"`,
  `scope:["admin:clear_quarantine","guardrail:skip:keyword"]`, `iat`, `exp`, `jti`.
- **Verify** (shared `tokens.py` verifier — sig + `exp` with small skew leeway →
  scope covers op → optional `jti` denylist), called at **two sites**: the
  **perimeter** verifies `admin:*` tokens for `/airlock/admin/*`; the **guardian
  pre-call hook** verifies the `X-Airlock-Capability` `guardrail:skip:*` token
  (CC-10/CC-11). Same verifier, different call site.
- **Revocation:** stateless ⇒ short TTL + optional `jti` denylist file for
  break-glass. Secret rotation invalidates all tokens ⇒ support a **previous
  secret** fallback (verify current+previous) for rolling rotation.

This **replaces** the forgeable `X-Airlock-Client` allowlist idea: identity now
lives inside a signed token — proven, scoped, expiring, still zero-infra.

---

## 5. Interfaces & personas

The split answers "who generates tokens": **the admin generates; the client
consumes.**

| | Operator / admin | Client / consumer |
|---|---|---|
| Holds | `AIRLOCK_MASTER_KEY`, `AIRLOCK_JWT_SECRET` | an LLM key + (if needed) a capability token |
| Interfaces | CLI mint verb, TUI (loopback) | normal requests; admin HTTP API if scripting |
| Authority | mint, full admin, live TUI actions | only what the token `scope` allows |

### 5.1 Token minting — new CLI verb (admin-only)

```
# guardrail-skip token — sub MUST be the client's authenticated key-derived id
# (key:<last8>), because skip authz compares sub to the validated bearer key (CC-11).
airlock admin mint-token --sub key:b35cf679 --scope guardrail:skip:keyword --ttl 1h
→ eyJhbGciOiJIUzI1Ni…

# admin-ops token — sub is the audit actor label (authz comes from the signature,
# not sub), so a human/automation name is fine here.
airlock admin mint-token --sub lme-ops --scope admin:clear_quarantine --ttl 15m
```

- **Local signing only** — `hmac`/`PyJWT` with the secret the operator already
  holds. No network call, no server, no DB ⇒ zero infra.
- Runs **as the admin**; the *token it emits* is handed to the client out-of-band
  (env var / secret manager / CI secret). Minting is an *admin* interface; its
  *output* is *for the client*.
- Optionally also a loopback-only `POST /airlock/admin/tokens`, but the CLI is
  primary/documented.

### 5.2 TUI live actions (loopback, no token)

Add keybindings via the screen's `BINDINGS` + action handlers in `overview.py`
(the cooldown the action targets is rendered at `:603`) — e.g. `c` = clear
quarantine on the selected provider → `POST /airlock/admin/...` over loopback →
Path A. Operator types nothing extra. The
mutation logs `admin_action` → tailer ingests → replica updates → the countdown
already drawn reflects the clear. No new TUI data path.

> **HTTPS-on-loopback wrinkle:** with native TLS on, the TUI hits
> `https://127.0.0.1` and meets the (likely self-signed) cert. Resolve by
> trusting the configured cert, or **skip-verify for `127.0.0.1` only**.

### 5.3 Admin HTTP API (remote, token)

```
curl -X POST https://airlock.internal/airlock/admin/providers/openai/clear-quarantine \
     -H "Authorization: Bearer <admin-scoped-jwt>" -d '{"mode":"probe"}'
```

### 5.4 Client request path — guardrail-skip token

Capability goes in a **dedicated header** so it never collides with LiteLLM's own
`Authorization` auth:

```
POST /v1/chat/completions
Authorization: Bearer <normal-LLM-key>     ← unchanged, LiteLLM consumes this
X-Airlock-Capability: <skip-scoped-jwt>     ← NEW, airlock consumes this
```

The **guardian pre-call hook** (not the perimeter middleware) verifies the token
via the shared `tokens.py` verifier, intersects its `guardrail:skip:*` scopes with
what config permits, and stamps the `GuardrailDecision` into
`data["metadata"]["airlock_guardrail_decision"]`; the content hooks read it
(CC-10). The perimeter middleware is not involved in the skip path.

---

## 6. How clients change (before/after)

- **Normal client (≈all traffic): no change.** Same endpoint + auth header; only
  the URL scheme changes *if* TLS is enabled.
- **Client that skips a guardrail:** add **one header**,
  `X-Airlock-Capability: <jwt>`. Admin mints once; client stores it like any
  secret.
- **Script doing admin ops:** call `/airlock/admin/*` with `Authorization: Bearer
  <jwt>`.
- **Operator at the TUI:** new keybindings; authorizes by being on the host.

Rollout is non-breaking: ship **off by default**, opt specific clients in by
handing them a scoped token.

---

## 7. Admin operations

Served in-proxy with direct `StateStore` access. New thread-safe mutators (under
the existing `RLock`), each emitting an `admin_action` JSONL record.

| Verb | Op | Authorize | Notes |
|---|---|---|---|
| `GET`  | `/providers`,`/circuits`,`/clients` | A or B(`admin:read`) | richer than `/health/circuits` |
| `POST` | `/providers/{p}/clear-quarantine`   | A or B(`admin:clear_quarantine`) | **`mode=probe` default**; clears provider-wide **and cascades to all `(client,p)` buckets** |
| `POST` | `/clients/{c}/providers/{p}/clear-quarantine` | A or B(`admin:clear_quarantine`) | **the precise UN-10 op** — clears one client→provider bucket |
| `POST` | `/providers/{p}/quarantine`         | A only (`loopback_only`) | manual arm |
| `POST` | `/models/{m}/reset-circuit`         | A or B(`admin:reset_circuit`) | close a tripped breaker |
| `POST` | `/clients/{c}/clear-backoff`        | A or B(`admin:clear_backoff`) | clear threat backoff |

**Provider clear must cascade to client→provider buckets (codex BLOCK,
2026-06-23).** A pinned request checks `ClientProviderState.quarantine_until`
(`guardian.py:224`) **before** provider-wide state (`:254`), and the UN-10
incident is a *single client's* per-client quarantine. So
`clear_provider_quarantine` clears the provider-wide state **and** every
`(client, provider)` bucket for that provider; `clear_client_provider_quarantine`
clears exactly one victim bucket. See umbrella **CC-8** for the mutator contract.

**Clear-quarantine defaults to half-open, not hard-clear:** drop the breaker to
HALF_OPEN (one probe allowed; success closes, failure re-arms) — mirrors the
existing `ModelState` recovery at `state.py:435`. A mistaken clear (credits *not*
actually topped up) self-corrects instead of re-storming. `mode=force` (blind
clear) is a separate, higher-privilege op. **Rate-limit** clear ops (reuse the
`threat_detector.py:53` decay primitive or a token bucket) so a caller can't
thrash the protection it bypasses.

---

## 8. Guardrail skips

Model the override as a per-request **effective mode per guardrail**, reusing the
existing `observe/shadow/enforce` vocabulary. **The resolver runs in the guardian
pre-call hook** (which has `data` + the request headers, the same place
`client_id` is derived), verifies `X-Airlock-Capability`, and stamps the result
into `data["metadata"]["airlock_guardrail_decision"]` — there is **no ASGI body
rewrite** by the perimeter (CC-10; the perimeter owns only `/airlock/admin/*`).
Resolution pipeline:

```
global config mode → request-class defaults (batch/mcp already skip some)
                   → capability override (only scopes the PDP granted)
                            ⇩
   GuardrailDecision { pii: enforce, keyword: observe, response_scan: off, … }
```

Each existing hook (`pii_guard`, `keyword_guard`, `response_scanner`,
`reasoning_stripper`) reads its entry from the decision instead of consulting env
directly. This also folds today's `batch`/`mcp` skip (`guardian.py:201`,
`monitor.py:187`) into the same resolver — those stop being special-cased booleans
and become class defaults.

Per-guardrail skippability with safe defaults: `pii_redact` non-skippable;
`keyword`/`response_scan` → downgrade to `observe`; `reasoning_strip` → `off`.

---

## 9. End-to-end flows

1. **Operator clears a draining quarantine (the original need):** TUI `c` →
   `POST /clear-quarantine {mode:probe}` over loopback → Path A → HALF_OPEN → probe
   passes → CLOSED; JSONL `admin_action` → TUI countdown clears. No credential.
2. **CI clears after a verified top-up:** `curl … Authorization: Bearer <jwt
   scope=admin:clear_quarantine ttl=15m>` → Path B → same op + audit.
3. **Benchmark needs keyword guard off:** admin
   `mint-token --sub key:b35cf679 --scope guardrail:skip:keyword --ttl 24h` (the
   `sub` is the harness's authenticated key-derived id, CC-11); harness sends
   `X-Airlock-Capability` → keyword guard downgraded to `observe` (still logged)
   for those requests only.
4. **Minting:** `airlock admin mint-token …` signs locally with
   `AIRLOCK_JWT_SECRET`; token distributed out-of-band.

---

## 10. Config (proposed)

```yaml
# Part A — native TLS (env-driven; shown for clarity)
tls:
  certfile_env: AIRLOCK_SSL_CERTFILE
  keyfile_env:  AIRLOCK_SSL_KEYFILE

admin:
  enabled: false                  # off → /airlock/admin/* = 404, capability hdr ignored
  trust_loopback: true            # Path A; warn if AIRLOCK_HOST != 127.0.0.1
  allow_insecure_tokens: false    # fail-closed: refuse start if tokens active on non-loopback HTTP w/o TLS
  behind_tls_proxy: false         # assert TLS is terminated upstream (skips the fail-closed check)
  jwt:
    secret_env: AIRLOCK_JWT_SECRET        # falls back to HKDF(AIRLOCK_MASTER_KEY)
    prev_secret_env: AIRLOCK_JWT_SECRET_PREV   # rolling rotation
    max_ttl: 24h
    denylist_file: ./logs/jti-denylist.txt     # optional break-glass
  rate_limits: { clear_quarantine: "1/30s" }
  operations:
    clear_quarantine: { enabled: true, default_mode: probe, scope: admin:clear_quarantine }
    force_quarantine: { enabled: true, scope: admin:force_quarantine, loopback_only: true }
    reset_circuit:    { enabled: true, scope: admin:reset_circuit }
    clear_backoff:    { enabled: true, scope: admin:clear_backoff }

guardrail_overrides:
  allow_capability_skip: false    # master flag
  capability_header: X-Airlock-Capability
  skippable:
    pii_redact:      { skippable: false }                   # never, by default
    keyword:         { skippable: true, downgrade_to: observe }
    response_scan:   { skippable: true, downgrade_to: observe }
    reasoning_strip: { skippable: true, downgrade_to: off }
  applies_to: [interactive]       # + batch when that path lands
```

---

## 11. Considerations & tradeoffs

- **Loopback + reverse proxy** — headline risk (§4.1). Block admin at the edge, or
  `trust_loopback:false`.
- **TLS protects the tokens.** A capability/admin JWT is a bearer credential —
  replayable until `exp` if sniffed. Part A and Part B are **coupled**: don't run
  token auth over plain HTTP on an exposed bind. TLS + short TTL + narrow scope
  keep leakage cheap.
- **Symmetric JWT** = zero infra but verifier-can-mint; fine (only airlock
  verifies). Revisit for multi-node.
- **Stateless revocation** → short TTL + `jti` denylist; rotation needs the
  prev-secret fallback.
- **Dedicated `AIRLOCK_JWT_SECRET`** decouples token lifetime from the LLM master
  key (independent rotation) — preferred over HKDF-from-master.
- **Skip = downgrade-to-observe, not silence**; `pii_redact` non-skippable —
  preserves audit.
- **Everything off by default; unknown → deny; admin disabled → 404** — keeps the
  rollout non-breaking.

---

## 12. Code layout & build order

New `airlock/admin/` package: `policy.py` (PDP + config model), `tokens.py` (JWT
mint/verify), `operations.py` (verbs over `StateStore`), `http.py`
(`install_admin_on_proxy_app()` + perimeter middleware), audit via
`enterprise_logger`. Plus `airlock/guardrails/overrides.py` (the resolver),
`state.py` mutators + `ingest_jsonl_record` handling of `admin_action`,
`tui/screens/overview.py` keybindings, `cli/main.py` `admin mint-token`.

1. **Native TLS** — env→CLI wiring + docs (small, independent, ship first).
2. `StateStore` mutators + `admin_action` JSONL record + `ingest_jsonl_record`
   support (no HTTP yet; unit-testable).
3. JWT mint/verify helper (`tokens.py`) + `airlock admin mint-token` CLI.
4. PDP + perimeter middleware + `/airlock/admin/*` with Path A + Path B.
5. TUI keybindings → admin HTTP client (loopback).
6. Guardrail resolver + `X-Airlock-Capability` skip (interactive); batch later.

---

## 13. Open decisions

1. **`AIRLOCK_JWT_SECRET` dedicated vs. HKDF-derive from master key** — recommend
   dedicated (independent rotation).
2. **TUI trust of the loopback cert under native TLS** — point at configured cert
   vs. skip-verify on `127.0.0.1` only (lean skip-verify, loopback-exclusive).
3. **Max token TTL cap** — 24h here; tighter (1–4h) is safer if re-minting is
   cheap.
