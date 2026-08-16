# 0.5.15 design plan — virtual-key management on the 0.6.0 identity/keystore contract

**Status:** deferred from 0.5.15 and transferred to 0.6.0 on 2026-08-16. This
historical design does not authorize an endpoint, persistence change, database
migration, or UI control. The 0.6.0 B5 keystore/identity foundation, threat
model, and minimum management slice remain HITL gates before implementation.

## Executive decision

The 0.5.14 carry-forward requirement (DFR-32/DAC-32) is viable only as an
adapter to the **Airlock-native `VirtualKeyStore` selected by 0.6.0**. It must
not turn on LiteLLM's `/key/*` management API, create a second credential
database, or use the client-controlled `X-Airlock-Client` header as tenant
identity. The planned management layer owns presentation, authenticated Admin
authorization, one-time secret delivery, and audit; the 0.6.0 identity/store
layer owns key authentication, policy state, revocation truth, budget state,
and enforcement.

This is intentionally a dependency-first plan: if the 0.6.0 contract changes
or its native authentication seam is not available, virtual-key management is
deferred again rather than substituting a new store.

## Evidence and dependency contract

The 0.6.0 design (`dev/notes/design-tenant-keys-budgets.md`) is DECIDE-ready
and records two load-bearing findings:

- LiteLLM virtual keys require a PostgreSQL-backed key-management setup. That
  violates Airlock's single-tenant/no-required-external-service default and
  would make LiteLLM the parallel durable source of truth.
- LiteLLM's `general_settings.custom_auth` hook can authenticate a presented
  key using an Airlock-native store before the normal master-key path. The
  native store therefore remains viable, but all management routes must remain
  Airlock-admin protected because custom auth does not supply the required
  governance policy by itself.

The agreed 0.6.0 ownership model is:

```
key secret presented to inference
  -> LiteLLM custom_auth adapter
  -> Airlock VirtualKeyStore (authoritative record / revocation / policy)
  -> authenticated ClientIdentity
  -> guardian + allowlist/routing + admission/budget + dispatch backstop

Admin/TUI management request
  -> Airlock Admin PDP (separate operator identity and exact scope)
  -> VirtualKeyManagement service
  -> same Airlock VirtualKeyStore
  -> redacted admin_action event / bounded read projection
```

The two identities are deliberately different:

| Identity | Source | Purpose | Never used for |
| --- | --- | --- | --- |
| Operator | Admin loopback or scoped capability JWT | create/read/update/revoke authority and audit actor | inference tenant attribution |
| Tenant/key | validated virtual-key secret → stable record id | model authorization, budgets, limits, request attribution | management authority |
| `X-Airlock-Client` | caller-controlled attribution header | optional display/diagnostics only | authorization, isolation, spending, revocation |

## Requirements to ratify or revise

### Functional requirements

- **DKR-1 — one authority.** Virtual-key policies, secret verifier, lifecycle,
  revocation, budget/limits, and owner/team metadata have exactly one durable
  authority: the 0.6.0 `VirtualKeyStore`. Admin/TUI are adapters, not stores.
- **DKR-2 — constrained creation.** An authorized operator can request a key
  with a label and a policy approved by 0.6.0 (allowed model aliases,
  budget/limit references, expiry, and owner/team identifier). Server-side
  validation rejects unknown aliases, invalid budgets, impossible expiry, and
  ambiguous ownership before persistence.
- **DKR-3 — one-time secret reveal.** The randomly generated bearer secret is
  returned only in the successful creation response over an authenticated,
  approved management transport. Subsequent read/list/update/revoke responses
  expose only a masked display prefix and stable non-secret identifier. A lost
  secret is replaced by creating/rotating a new key, never recovered.
- **DKR-4 — lifecycle.** List/read views show bounded masked status, expiration,
  allowed-alias summary, current policy/budget state, spend/limit state and
  `revoked_at`; authorized revocation takes effect at authentication before a
  later upstream request. Policy updates must obey the same 0.6.0 validation
  and enforcement invariants as creation.
- **DKR-5 — exact management authorization.** Explicit scopes are required:
  `admin:keys:read`, `admin:keys:create`, `admin:keys:update`, and
  `admin:keys:revoke` (or a formally reviewed equivalent). The existing scope
  matcher is exact-string based, so a decorative `admin:keys:*` must not be
  claimed as a wildcard without matcher work and review.
- **DKR-6 — audit and read model.** Successful lifecycle mutations produce one
  redacted `admin_action` record with actor, operation, non-secret key id,
  policy delta summary, and timestamp. Reads are bounded, source-labelled
  snapshots that contain no bearer material and run off the inference hot path.
- **DKR-7 — availability semantics.** An unavailable key-authentication store
  fails closed for a presented virtual key (the 0.6.0 isolation decision);
  management reports unavailable and performs no partial mutation. Master-key
  behavior and anonymous inference retain their separately defined contracts.
- **DKR-8 — no accidental LiteLLM key plane.** Airlock disables or blocks
  LiteLLM `/key/*`, `/user/*`, and `/team/*` management routes for virtual-key
  principals and does not expose the LiteLLM admin UI as Airlock's key UI.

### Non-functional/security requirements

- Default deployments retain no mandatory PostgreSQL, Redis, or external secret
  manager. A configured shared backend is a 0.6.0 store deployment choice, not
  a TUI feature.
- Secret generation uses a CSPRNG; the store retains only a non-reversible,
  domain-separated verifier/identifier suitable for lookup, never plaintext.
  The exact construction (including pepper/key rotation) is a 0.6.0 crypto
  design decision and must receive security review before coding.
- Secret values are prohibited from JSONL, callback/event payloads, FathomDB,
  S3/SQL exporters, exception text, metrics labels, traces, URLs, process
  arguments, config files, CLI history, TUI state/snapshots, tests, and audit
  records.
- Tenant policy always beats routing resilience: allowed-model candidate sets
  are filtered and the per-dispatch backstop rejects an off-list final model;
  LiteLLM fallbacks must not bypass authorization.
- The feature is opt-in. Anonymous/no-key requests remain behaviorally
  compatible, verified by the 0.6.0 replay oracle.

## Acceptance criteria for an implementation proposal

1. A valid Admin principal with `admin:keys:create` creates a policy-valid key,
   receives one secret only once, and thereafter sees a masked record; the
   secret is absent from every persisted, observed, and rendered artifact.
2. Missing, expired, invalid, read-only, or wrong-scope operator credentials
   cannot list, create, update, reveal, or revoke a key; a tenant key cannot
   reach any Admin/key-management route.
3. A valid virtual key authenticates through the native 0.6.0 auth seam and
   yields its authenticated identity; a forged `X-Airlock-Client` does not
   change its enforcement identity, quota, breaker bucket, or attribution
   authority.
4. Revocation is durable and immediately denies new keyed requests before
   dispatch. Concurrent use/revocation behavior is specified, tested, and
   leaves no successful post-revocation authentication ambiguity.
5. Model/budget/limit display agrees with the authoritative store; model
   authorization covers alias resolution, smart/cost routing, Airlock failover,
   and every LiteLLM retry/fallback attempt. No off-list provider call occurs.
6. A store outage gives a bounded failure for keyed inference and management,
   with no anonymous fallback. Anonymous and master-key contracts are tested
   separately and unchanged where 0.6.0 says they are unchanged.
7. List views are capped/paginated, redact stable ids appropriately, label their
   data source/freshness, and do not access FathomDB or subscribe a UI worker
   to request callbacks.
8. Tests include secret-leak scans across structured logs/events/traces/TUI
   snapshots, creation idempotency/retry policy, update validation, one-time
   reveal, revocation race, restart persistence, unauthorized paths, and the
   existing anonymous golden replay.
9. Security and architecture reviewers approve the verifier construction,
   storage backend, route gating, token transport, and all migration/rollback
   semantics before a feature flag is enabled.

## Proposed management design

### API and UI shape

The first approved slice should prefer a small authenticated Admin API used by
both CLI and TUI. The TUI is a client of that API; it never writes configuration
or database files directly.

| Operation | Proposed outcome | Required authority |
| --- | --- | --- |
| List/read | bounded masked records, policy/spend/status/source | `admin:keys:read` |
| Create | validates policy, persists record, returns one-time secret exactly once | `admin:keys:create` |
| Update policy | validated patch with concurrency/version rule; no secret return | `admin:keys:update` |
| Revoke | idempotent lifecycle transition and audit | `admin:keys:revoke` |
| Rotate | explicit create-new + distribute + revoke-old workflow initially | create + revoke; no silent in-place secret replacement |

Do not provide a text field for a provider secret or virtual-key secret in the
TUI. The TUI can render the creation secret in an intentional one-time modal
with copy confirmation, then discard it from application state; secure delivery
outside the terminal remains the operator's responsibility. The precise terminal
clipboard policy requires HITL because terminals, multiplexers, and clipboard
integrations have different retention behavior.

### Store and transactional rules

`VirtualKeyManagement` calls the same store interface selected in 0.6.0. Its
create transaction must atomically commit the record, verifier, initial policy
revision, and audit-intent/outbox entry (or report an explicitly recoverable
state). Revocation must make the authentication lookup deny before reporting
success. If a backing store later supports optimistic revisions, update/revoke
uses them to avoid lost policy changes; otherwise concurrent semantics must be
conservatively serialized and documented.

The management API must not attempt to synthesize spend from JSONL. It consumes
the 0.6.0 authoritative per-key accumulator/view and labels unavailable/stale
conditions. This preserves the architecture rule that UI reads are bounded and
off the inference hot path.

## External research and library assessment

| Source/library | Relevant capability | Alignment assessment |
| --- | --- | --- |
| [LiteLLM virtual keys documentation](https://docs.litellm.ai/docs/proxy/virtual_keys) | Documents `/key/generate`, spend, blocking, policy hooks, and states that virtual-key setup needs PostgreSQL + a master key. | **Partial only.** Reuse its provider translation/custom-auth integration where 0.6.0 characterized it; do not use its persistence, routes, UI, or admin ownership. |
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | Current embedded proxy substrate and custom-auth hook surface. | **Existing dependency, partial.** Preserve version-pinned adapter characterization and block upstream management routes for tenant keys. |
| [HashiCorp Vault Transit](https://developer.hashicorp.com/vault/docs/secrets/transit) / [hvac](https://github.com/hvac/hvac) | Envelope encryption, key versioning, and a Python client for an operator-managed external KMS. | **Optional integration, not baseline.** Could protect a configured store-encryption key in enterprise deployments; it must not become mandatory or a second virtual-key database. |
| [argon2-cffi](https://github.com/hynek/argon2-cffi) | Password hashing library. | **Not selected by default.** Key verification needs a high-entropy bearer-token lookup and hot-path budget; a 0.6.0-reviewed keyed verifier/pepper design is required instead of importing password-hash semantics by assumption. |
| Python `secrets`, `hmac`, `hashlib` | CSPRNG and keyed hashing primitives already available in Airlock's runtime. | **Candidate baseline only after crypto review.** Avoids a new dependency but is not a substitute for defining rotation, domain separation, and durable-store behavior. |

The key architectural fact from LiteLLM's official documentation is material:
its virtual-key setup assumes PostgreSQL. Airlock's 0.6.0 decision explicitly
rejects making that a prerequisite for the single-container default; therefore
using LiteLLM key CRUD would be a false shortcut, even if it could complete the
UI endpoints quickly.

## Architectural alignment and blast radius

**Alignment verdict: aligned only behind the ratified 0.6.0 native seam.** It
extends the existing three planes rather than duplicating them: `StateStore`/
store ownership for durable key state, the guardian/custom-auth path for
inference enforcement, `RequestEvent`/recorder for attributable observability,
and Admin PDP/TUI client for operator management. The plan is misaligned if it
adds a TUI-owned SQLite file, treats LiteLLM `/key/*` as the source of truth,
allows a request header to select tenant policy, or adds UI callbacks to the
inference path.

| Area | Future change | Blast radius | Controls |
| --- | --- | --- | --- |
| Authentication/identity | custom-auth adapter + native virtual-key resolution | Critical: tenant isolation | fail closed, forged-header adversarial tests, master/admin route denial |
| Guardian/routing/fallback | allowed-set filtering and dispatch backstop | Critical: provider authorization | end-to-end fallback tests; policy outranks resilience |
| Store/spend lifecycle | native durable records, revocation, key spend | High: correctness and availability | one authority, atomic rules, restart/reconciliation tests |
| Admin API/CLI/TUI | scoped lifecycle operations and masked views | High: secret disclosure/control authority | exact scopes, bounded output, redacted audits, no direct store access |
| Callbacks/logging | identity/policy attribution | High: PII/secret leak risk | canonical event fields and artifact-leak tests |
| Anonymous inference | no intended change | Must be zero | golden replay and regression suite |

## Rollout and verification plan

1. **0.6.0 dependency gate:** confirm the native store interface, custom-auth
   adapter, identity re-key, allowlist/fallback backstop, and store-outage
   behavior are implemented and reviewed. Otherwise stop; do not backfill with
   LiteLLM CRUD.
2. **HITL requirement ratification:** decide allowed policy fields, secret
   verifier/encryption construction, token-transport profile, audit retention,
   terminal one-time-reveal behavior, and whether updates ship with the first
   slice.
3. **RED contracts:** add pure store/management/PDP tests first, then adversarial
   tests for raw-secret leakage, forged header identity, key access to admin
   routes, stale write/revoke, fallback escape, and store outage.
4. **GREEN slice:** introduce default-off management endpoints and a CLI before
   the TUI screen; keep an audit outbox/recovery story explicit. Add TUI only
   after the API response schema is stable and snapshot-safe.
5. **Canary:** use a disposable tenant and non-sensitive configured alias;
   observe masked read, one-time delivery, allowed request, revocation denial,
   and redacted audit. Do not use a live master key or customer prompts as test
   evidence.
6. **Rollback:** disable the management feature while retaining store records;
   revocation remains enforceable. Never roll back by deleting a live credential
   store. Rotate affected credentials/pepper according to the approved incident
   procedure if a secret-handling failure is discovered.

## Remaining HITL questions

- Should the first management slice ship only create/list/revoke, leaving policy
  update and rotation for a later version to minimize irreversible semantics?
- What is the approved default store backend and backup/restore contract for a
  single-container deployment, and when may a shared-store configuration be
  supported?
- What maximum key-policy expressiveness is justified initially without making
  the key surface a second routing language?
- Which secure out-of-band distribution mechanism is recommended for the
  one-time secret, and should terminal clipboard integration be disabled by
  default?
