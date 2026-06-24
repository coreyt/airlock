# Admin API

The admin API is Airlock's control plane for **live protection state**: clearing
a provider quarantine after a verified credit top-up, resetting a tripped model
circuit, or clearing a client backoff — without restarting the proxy or flipping
a global env flag.

It is **off by default**. When disabled, every `/airlock/admin/*` route returns
`404` (Airlock does not confirm the routes even exist), and capability headers are
ignored. A config-free deploy behaves exactly as it did before 0.5.0.

The TUI's clear-quarantine keybinding (see [TUI Dashboard](tui.md)) is just a
loopback client of this same API — the admin API is the foundation, the TUI is one
caller.

## Enabling it

Add an `admin:` block to `config.yaml`:

```yaml
admin:
  enabled: true            # off → /airlock/admin/* returns 404, capability hdr ignored
  trust_loopback: true     # treat loopback connections as the operator (Path A)
  allow_insecure_tokens: false   # fail-closed guard for token auth over plaintext
  behind_tls_proxy: false  # assert TLS is terminated by an upstream proxy
```

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Master switch. `false` → all admin routes return `404`. |
| `trust_loopback` | `true` | A connection from `127.0.0.1`/`::1` is the operator tier, no credential (Path A). |
| `allow_insecure_tokens` | `false` | Permit token auth on a non-loopback bind without TLS (downgrades the fail-closed startup refusal to a warning). |
| `behind_tls_proxy` | `false` | Assert that TLS is terminated by an upstream reverse proxy, so the fail-closed check is satisfied. |

### Fail-closed on insecure token auth

Bearer tokens — both admin JWTs and guardrail-skip capabilities — are replayable
until they expire if sniffed over plaintext. So **at startup**, if the admin API
(or capability skips) is enabled **and** the bind is non-loopback (`AIRLOCK_HOST`
is not `127.0.0.1`/`::1`/`localhost`) **and** native TLS is off (`AIRLOCK_SSL_*`
unset), Airlock **refuses to start**.

Resolve it by one of:

- terminating TLS in Airlock itself — set `AIRLOCK_SSL_CERTFILE` /
  `AIRLOCK_SSL_KEYFILE` (see [native TLS in the Operations guide](../operations.md#native-tls));
- asserting an upstream TLS-terminating proxy — `admin.behind_tls_proxy: true`;
- explicitly accepting the risk — `admin.allow_insecure_tokens: true` (logs a loud
  warning instead of refusing).

Loopback-only deploys are unaffected.

## Auth model

A request is admitted if **either** path succeeds:

- **Path A — loopback is the operator.** A connection from `127.0.0.1`/`::1` is
  treated as the operator tier with no credential, as long as `trust_loopback` is
  on. Reaching loopback means shell access on the host, which already implies proxy
  privilege. This is the TUI path.
- **Path B — Bearer token.** `Authorization: Bearer <token>`, where the token is
  either the **master key** (`AIRLOCK_MASTER_KEY`, full admin + token minting) or a
  **capability JWT** carrying the scope the operation requires.

Some operations are **loopback-only** regardless of token (e.g. manual quarantine).

> **Reverse-proxy caveat.** A reverse proxy forwarding to Airlock on loopback makes
> *every* request look local, which would grant Path A to the world. When you expose
> the proxy, either block `/airlock/admin/*` at the edge, or set
> `trust_loopback: false` to force Path B even locally. Airlock warns at startup if
> `trust_loopback` is on while `AIRLOCK_HOST` is not loopback.

### Scopes

Each operation requires a scope. A capability token carries a list of scopes; the
master key (and any loopback operator) satisfies all of them.

| Scope | Grants |
|---|---|
| `admin:read` | The read-only `GET` endpoints |
| `admin:clear_quarantine` | Clear a provider or client→provider quarantine |
| `admin:reset_circuit` | Reset a model circuit |
| `admin:clear_backoff` | Clear a client threat backoff |
| `admin:force_quarantine` | Manually quarantine a provider (loopback-only) |

## Minting capability tokens

Tokens are short-lived HS256 JWTs signed with `AIRLOCK_JWT_SECRET` (which falls
back to an HMAC derivation from `AIRLOCK_MASTER_KEY` when unset). Set a dedicated
`AIRLOCK_JWT_SECRET` so token lifetime is decoupled from your LLM master key, and
set `AIRLOCK_JWT_SECRET_PREV` during a rolling secret rotation so in-flight tokens
verify against the previous secret too.

Mint with the CLI — it **signs locally** (no network, no server, no DB) using the
secret the operator already holds:

```bash
# Admin-ops token — --sub is an audit-actor label (authorization comes from the
# signature + scope, not the sub).
airlock admin mint-token --sub lme-ops --scope admin:clear_quarantine --ttl 15m
→ eyJhbGciOiJIUzI1Ni…

# Multiple scopes on one token:
airlock admin mint-token --sub ci-bot \
  --scope admin:read --scope admin:clear_quarantine --ttl 1h
```

- `--ttl` accepts durations like `15m`, `1h`; the cap is **24h**.
- Minting runs **as the admin**; the token it emits is handed to the client
  out-of-band (env var, secret manager, CI secret).

> Guardrail-skip tokens (`guardrail:skip:*`) are minted the same way but their
> `--sub` **must** be the client's authenticated key-derived id (`key:<last8>`).
> See [Guardrails → Per-request guardrail skips](guardrails.md#per-request-guardrail-skips).

## Endpoints

All routes are under `/airlock/admin/` and only exist when `admin.enabled: true`.

### Read state

| Method | Path | Scope |
|---|---|---|
| `GET` | `/airlock/admin/providers` | `admin:read` |
| `GET` | `/airlock/admin/clients` | `admin:read` |
| `GET` | `/airlock/admin/circuits` | `admin:read` |

These return a richer view of live protection state than the read-only
`GET /health/circuits`.

```bash
# Loopback operator — no credential needed:
curl http://localhost:4000/airlock/admin/providers

# Remote, with a scoped token:
curl https://airlock.internal/airlock/admin/circuits \
     -H "Authorization: Bearer <admin:read-jwt>"
```

### Clear a provider quarantine

Clears the provider-wide quarantine **and cascades** to every `(client, provider)`
bucket for that provider — "unblock everyone on openai".

```bash
curl -X POST https://airlock.internal/airlock/admin/providers/openai/clear-quarantine \
     -H "Authorization: Bearer <admin:clear_quarantine-jwt>" \
     -H "Content-Type: application/json" \
     -d '{"mode":"probe"}'
```

Scope: `admin:clear_quarantine`. Body `mode`:

- **`probe`** (default) — drop the breaker to **half-open**: the next request is
  admitted as a one-shot probe; a success closes the circuit, a failure re-arms it
  on the policy cooldown. A mistaken clear (credits *not* actually topped up)
  self-corrects instead of re-storming.
- **`force`** — blind clear; lifts the quarantine immediately with no probe gate.

### Clear one client→provider quarantine

Clears exactly one victim bucket — the precise operation for a single client's
per-client quarantine, leaving the rest of the provider's clients untouched.

```bash
curl -X POST \
  https://airlock.internal/airlock/admin/clients/key:b35cf679/providers/openai/clear-quarantine \
     -H "Authorization: Bearer <admin:clear_quarantine-jwt>" \
     -d '{"mode":"probe"}'
```

Scope: `admin:clear_quarantine`. Same `mode` semantics as above.

### Reset a model circuit

Closes a tripped per-model circuit breaker.

```bash
curl -X POST https://airlock.internal/airlock/admin/models/gpt-5.4/reset-circuit \
     -H "Authorization: Bearer <admin:reset_circuit-jwt>"
```

Scope: `admin:reset_circuit`.

### Clear a client backoff

Clears a client's threat-detector backoff.

```bash
curl -X POST https://airlock.internal/airlock/admin/clients/key:b35cf679/clear-backoff \
     -H "Authorization: Bearer <admin:clear_backoff-jwt>"
```

Scope: `admin:clear_backoff`.

### Manually quarantine a provider

Manually arms a provider quarantine (break-glass).

```bash
curl -X POST http://localhost:4000/airlock/admin/providers/openai/quarantine
```

Scope: `admin:force_quarantine`. **Loopback-only** — there is no remote/token path
for this operation.

## Audit log

Every successful mutation emits an `admin_action` record into the same JSONL log
stream as request records (`AIRLOCK_LOG_DIR`). The record *is* the audit trail, the
crash-recovery entry, and the channel the TUI's own state replica reads to converge
on the change — the mutation and its audit record are one object.

Each `admin_action` record carries the actor (the token `sub` or `loopback`), the
operation, its target (provider / client / model), the mode, and a timestamp. Filter
the logs for them:

```bash
grep '"record_type": "admin_action"' logs/airlock-$(date +%Y-%m-%d).jsonl \
  | python -m json.tool
```

> Request records carry `"record_type": "request"` (treated as the default when the
> key is absent, for back-compat with pre-0.5.0 logs).

## Native TLS

The admin API and capability tokens are bearer credentials, so they want TLS. You
can terminate it in Airlock itself rather than only at a reverse proxy — see
[native TLS in the Operations guide](../operations.md#native-tls).
