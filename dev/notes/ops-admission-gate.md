# Ops Guide: Admission Gate (0.5.5+)

The admission gate is an **off-by-default** per-client rate limiter built into the
guardian pre-call hook. When enabled it protects all clients from noisy-neighbor
bursts by shedding excess requests with `429 Too Many Requests` + `Retry-After`.

---

## Quick Start

Add to your `config.yaml`:

```yaml
airlock_settings:
  admission:
    enabled: true
    rpm: 60
```

Restart the proxy. The gate is active for all clients immediately. No other changes
are required.

To enable without touching config, set an environment variable before starting:

```bash
AIRLOCK_ADMISSION='{"enabled": true, "rpm": 60}' airlock start
```

---

## Configuration Reference

All fields live under `airlock_settings.admission`. All are optional; see defaults below.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Master switch. Gate is a no-op when `false`. |
| `rpm` | int | `60` | Max requests per 60-second rolling window, per client. |
| `concurrency` | int | `10` | Max concurrent in-flight requests per client. |
| `boost_multiplier` | float | `1.5` | RPM multiplier applied to clients with `PrioritySignal.boost=True`. |

**Environment override** — `AIRLOCK_ADMISSION` (JSON string) takes precedence over the
config block, which takes precedence over defaults. Only fields present in the JSON are
overridden; omitted fields use config/defaults.

Example: `AIRLOCK_ADMISSION='{"enabled": true, "rpm": 30, "concurrency": 5}'`

---

## Choosing rpm and concurrency Values

**RPM (`rpm`):**
- Start with your observed p95 request rate per client (check your telemetry or
  the enterprise log's `requests_per_minute` field).
- Add 20–30% headroom for legitimate spikes.
- Example: if a typical client sends 40 req/min, `rpm: 55` gives a 37% burst buffer.
- Prioritized clients (boost=True) get `rpm × boost_multiplier` automatically
  (default: 1.5×, so `rpm: 60` → 90 for boosted clients).

**Concurrency (`concurrency`):**
- Set to the number of provider connections you want to allocate per client.
- A conservative starting point: `total_provider_slots / expected_client_count`.
- Example: if your provider allows 50 concurrent connections and you have 10 clients,
  `concurrency: 5` gives equal allocation.
- Note: the current implementation uses a non-blocking peek (see Limitations below).
  The value still provides burst detection but is not an exact hard cap.

---

## What Operators See When the Gate Fires

### Log line

When a request is shed, the guardian emits a `ValueError` that LiteLLM converts to a
429. The proxy access log will show:

```
429 POST /v1/chat/completions  client=<client_id>  reason="Too many requests — Retry-After: 42.3s"
```

### Metadata stamp

Regardless of whether the request is shed, `data["metadata"]["airlock_admission"]`
is populated with the gate decision. On a shed this contains the reason string. This
is visible in the enterprise log's metadata field.

### Planned response header

`X-Airlock-Admission` will carry the shed reason in a future release. The metadata
is already stamped; the header wiring is a known follow-up.

---

## Retry-After Semantics for Client Developers

When your client receives `HTTP 429`, the response includes:

```
Retry-After: <seconds>
```

This is computed as `max(0.1, 60 - (now % 60))` — the time remaining in the current
60-second rolling window. This is a precise estimate, not a fixed guess.

**Recommended client behavior:**
1. Read the `Retry-After` header value (float seconds).
2. Wait at least that long before retrying.
3. Apply a small jitter (e.g., `Retry-After + random(0, 1)`) to avoid thundering herd.
4. Do NOT retry immediately — the gate will shed the retry too if the window has not reset.

---

## Fail-Open Behavior

If the admission gate encounters an internal error (e.g., a threading exception, an
unexpected state), it logs a `WARNING` and **admits the request**. The gate will never
block traffic due to its own malfunction.

```
WARNING airlock.fast.admission: admission gate error — failing open
```

If you see this log line, check the exception details that follow. The most common cause
is a misconfigured `AdmissionConfig` (e.g., non-integer `rpm`).

---

## Known Limitations

1. **Concurrency cap is a peek, not a hard cap.** The current implementation reads
   `asyncio.Semaphore._value` without acquiring/releasing the semaphore. This means:
   - Two simultaneous requests could both see `_value > 0` and both be admitted if they
     arrive in the same event-loop tick.
   - The cap works correctly as a burst detector but may not enforce the exact integer
     limit under extreme concurrency.
   - Full `asyncio.Semaphore` acquire/release is planned for a follow-up release.

2. **`X-Airlock-Admission` response header not yet wired.** The shed reason is stamped
   in `data["metadata"]["airlock_admission"]` and visible in the enterprise log, but it
   is not yet propagated to the HTTP response headers. Planned for a follow-up.

3. **Single-process scope.** The `AdmissionStore` is an in-process dict-of-deques.
   In a multi-worker deployment (multiple `--num_workers` processes), each worker
   maintains its own counter. Effective RPM = configured RPM × worker count.
   A Redis-backed store via the DualCache seam is planned for a follow-up.
