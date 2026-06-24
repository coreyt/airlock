# Rate Limiting & the Circuit Breaker

When a provider rate-limits Airlock (HTTP 429 / quota errors), Airlock's
per-client **circuit breaker** quarantines the affected client→provider pair for a
cooldown so it stops hammering an upstream that is already refusing it. While a
quarantine is in force, Airlock blocks the client itself with a descriptive **429**
that tells the client exactly how long to back off.

This guide covers two things: the **429 contract** clients see, and the
**circuit-breaker** that produces it (and how to tune it per client).

## The 429 contract

An Airlock breaker block returns **HTTP 429** with a `Retry-After` header and an
OpenAI-shaped body. The same handler also shapes *passthrough* provider 429s (where
the provider returned 429 and the breaker did not block), so clients get a backoff
signal either way.

### Headers

| Header | Value | Meaning |
|---|---|---|
| `Retry-After` | `<seconds>` | Whole seconds to wait before retrying. Honor this. |
| `X-Airlock-Provider-State` | `quarantined` | The provider is quarantined for this client. |
| `X-Airlock-Block-Scope` | `client_provider` \| `provider` \| `model` | What scope the block applies to. |

### Body

The body stays OpenAI-compatible (`{"error": {message, type, param, code}}`) with an
extra `airlock` sub-object:

```json
{
  "error": {
    "message": "Airlock paused requests to openai for this client to protect upstream standing. Retry after 30s.",
    "type": "airlock_circuit_breaker",
    "code": "provider_blocked",
    "param": null,
    "airlock": {
      "scope": "client_provider",
      "provider": "openai",
      "cooldown_seconds": 30,
      "retry_after": 30,
      "reason": "litellm.RateLimitError: ...quota...",
      "source": "circuit_breaker"
    }
  }
}
```

`type` distinguishes the two cases:

| `type` | `airlock.source` | When |
|---|---|---|
| `airlock_circuit_breaker` | `circuit_breaker` | Airlock's breaker is blocking the client. |
| `provider_rate_limit` | `provider` | Passthrough: the provider returned 429; `Retry-After` comes from the provider's own headers when present, else a default. |

### What your client should do

Honor `Retry-After`. **Do not tight-loop** — retrying every 1–9 seconds against a
quarantined provider just burns the whole cooldown window on blocked requests.

```python
resp = client.post(url, json=payload)
if resp.status_code == 429:
    wait = int(resp.headers.get("Retry-After", "30"))
    time.sleep(wait)        # back off; do not retry immediately
    resp = client.post(url, json=payload)
```

> If a client appears to receive an empty / `None` response under load, it is almost
> always an un-handled 429 — inspect the status code and honor `Retry-After`. See
> [Troubleshooting](../troubleshooting.md).

## The per-client circuit breaker

The breaker arms on a **threshold** of 429s within a window, per client→provider
pair. The shipped defaults reproduce the historical one-strike behaviour, so a
config-free deploy is unchanged.

| Setting | Default | Meaning |
|---|---|---|
| `rate_limit_threshold` | `1` | 429s within the window before the pair is quarantined. |
| `rate_limit_window_seconds` | `300` | Sliding window for counting 429s. |
| `client_cooldown_seconds` | `300` | How long a client→provider quarantine lasts. |
| `provider_cooldown_seconds` | `300` | How long a provider-wide quarantine lasts. |
| `provider_escalation_client_threshold` | `2` | Distinct rate-limited clients within the window before the *whole provider* is quarantined for everyone. |

### Configuring it

Add an `airlock_settings.circuit_breaker` block to `config.yaml`:

```yaml
airlock_settings:
  circuit_breaker:
    # global defaults (apply when a client has no override)
    rate_limit_threshold: 1
    rate_limit_window_seconds: 300
    client_cooldown_seconds: 300
    provider_cooldown_seconds: 300
    provider_escalation_client_threshold: 2
    clients:                          # per-client-key overrides
      "key:b35cf679":                 # a trusted first-party batch client
        rate_limit_threshold: 8       # tolerate bursts before tripping
        client_cooldown_seconds: 30   # short cooldown
        escalation_exempt: true       # never trip the provider for everyone else
```

Per-client keys are `key:<last8>` — the last 8 characters of the client's
authenticated API key. Each client entry accepts:

| Per-client key | Meaning |
|---|---|
| `rate_limit_threshold` | Override the threshold for this client. |
| `client_cooldown_seconds` | Override the cooldown for this client. |
| `escalation_exempt` | When `true`, this client's 429s do **not** count toward provider-wide escalation — a trusted batch client hammering its own quota will not quarantine the provider for everyone else. |
| `disabled` | When `true`, the breaker is skipped entirely for this client (it is never quarantined from 429s). |

Precedence: **per-client override → global default → built-in constant**.

### Env override

The same shape is available as JSON in `AIRLOCK_BREAKER_OVERRIDES`, which takes
precedence over the config block:

```bash
AIRLOCK_BREAKER_OVERRIDES='{"defaults":{"rate_limit_threshold":2},"clients":{"key:b35cf679":{"rate_limit_threshold":8,"client_cooldown_seconds":30,"escalation_exempt":true}}}'
```

Malformed JSON falls back to defaults with a logged warning — it does not crash
startup. Breaker config is read **once at startup**; changing it requires a restart.

### Clearing a quarantine early

A quarantine drains by wall-clock. If the underlying cause is resolved sooner (e.g.
a verified credit top-up), an operator can clear it immediately via the
[Admin API](admin-api.md) (`clear-quarantine`, default `mode=probe` for a self-correcting
half-open probe) or the TUI's `c` keybinding. See
[Troubleshooting → stale quarantine](../troubleshooting.md).
