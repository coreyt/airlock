# Airlock Troubleshooting

Common issues and solutions for Airlock deployments.

## Startup Issues

### "config.yaml not found"

```
ERROR: config.yaml not found. Set AIRLOCK_CONFIG or place it in the project root.
```

Airlock checks these paths in order:
1. `$AIRLOCK_CONFIG` environment variable
2. `./config.yaml` (current working directory)
3. `<project-root>/config.yaml`
4. `/etc/airlock/config.yaml`

**Fix:** Set `AIRLOCK_CONFIG` to the absolute path, or `cd` to the directory containing `config.yaml`.

### "Missing environment variables for MCP servers"

```
ERROR: Missing environment variables for MCP servers:
  MCP server 'ado_mcp' requires ADO_PAT (set in .env or shell environment)
```

MCP server definitions in `config.yaml` use `os.environ/VAR_NAME` references. The referenced variables must be set.

**Fix:** Add the missing variables to `.env` or export them in your shell. If you don't use the MCP server, remove or comment it out in `config.yaml`.

### "AIRLOCK_MASTER_KEY is set to the default value"

The proxy is using `sk-airlock-change-me`. This works but is insecure.

**Fix:** Generate a strong key and set it in `.env`:
```bash
python -c "import secrets; print(f'AIRLOCK_MASTER_KEY={secrets.token_urlsafe(32)}')" >> .env
```

### "model_list is missing or not a list"

Config validation found no models defined. The proxy will start but won't serve any requests.

**Fix:** Add at least one model to `config.yaml`. Run `airlock init` to generate a template.

## Runtime Issues

### Requests return 401 Unauthorized

The client is not sending the correct master key.

**Check:**
```bash
curl -H "Authorization: Bearer YOUR_KEY" http://localhost:4000/health
```

**Common causes:**
- Client API key doesn't match `AIRLOCK_MASTER_KEY`
- Key has leading/trailing whitespace
- Client is sending to the wrong port

### Requests return 500 Internal Server Error

**Check logs:**
```bash
tail -f logs/airlock-$(date +%Y-%m-%d).jsonl | python -m json.tool
```

**Common causes:**
- Provider API key is invalid or expired
- Provider is rate-limiting (check for 429 responses in logs)
- Model name doesn't match any entry in `config.yaml`'s `model_list`

### PII guard blocks legitimate requests

Presidio may flag content that isn't actually PII (false positives).

**Diagnosis:**
1. Check logs for `airlock_pii` metadata — which entity types were detected
2. Narrow the entity list: `AIRLOCK_PII_ENTITIES=CREDIT_CARD,US_SSN`
3. Switch guardrail mode to `observe` to log without blocking while you tune

### Circuit breaker is open for a model

The circuit breaker opens after repeated failures to a provider.

**Check state:**
- TUI dashboard shows circuit breaker status per model
- Prometheus metric: `airlock_circuit_breaker_state`

**Resolution:**
- Wait for the half-open period (automatic)
- Fix the underlying provider issue (expired key, quota exceeded)
- Restart the proxy to reset all circuit breakers

### A provider quarantine is stale after a credit top-up

A provider was quarantined by the circuit breaker, but you've since fixed the
underlying cause (e.g. topped up credits). The quarantine otherwise drains only by
wall-clock.

**Fix:** Clear it without waiting:

- **TUI** — on the Overview screen, highlight the provider row and press `c`.
- **Admin API** — `POST /airlock/admin/providers/<provider>/clear-quarantine` with
  `{"mode":"probe"}` (a self-correcting half-open probe; use `"force"` for a blind
  clear). See [Admin API](guide/admin-api.md).

Both require `admin.enabled: true`.

### Admin API returns 404

`/airlock/admin/*` returns `404` even though you're hitting the right path.

**Cause:** The admin API is off by default; when disabled the routes return `404` on
purpose (Airlock doesn't confirm they exist).

**Fix:** Set `admin.enabled: true` in `config.yaml` and restart. See
[Admin API → Enabling it](guide/admin-api.md#enabling-it).

### A client sees empty / `None` responses under load

A client appears to get an empty or `None` response instead of an error when a
provider is busy.

**Cause:** It's almost always an **HTTP 429** the client isn't handling — Airlock's
circuit breaker is blocking the client→provider pair to protect the upstream.

**Fix:** Have the client inspect the status code and honor the `Retry-After` header
instead of tight-looping. The body is OpenAI-shaped with
`type: "airlock_circuit_breaker"`. See the
[429 contract](guide/rate-limiting.md#the-429-contract).

### A client key is being quarantined too aggressively

A trusted batch client trips the breaker on a handful of genuine 429s and then can't
make progress.

**Fix:** Raise its threshold or exempt it from provider-wide escalation in
`airlock_settings.circuit_breaker.clients` (or `AIRLOCK_BREAKER_OVERRIDES`). See
[Rate Limiting → configuring the breaker](guide/rate-limiting.md#configuring-it).

### Keyword guard blocks unexpected content

**Check:** `AIRLOCK_BLOCKED_KEYWORDS` may contain terms that match too broadly.

**Fix:** Review and narrow the keyword list. Keywords are matched case-insensitively as substrings.

## Performance Issues

### High latency on first request

The spaCy NLP model loads on first PII scan. Subsequent requests are fast.

**Mitigation:** The model loads at import time. Ensure the proxy is warmed up before receiving traffic (the readiness probe handles this in k8s).

### Logs consuming too much disk

**Check:**
```bash
du -sh logs/
ls -lh logs/ | head -20
```

**Fix:** Tune retention and rotation:
```bash
AIRLOCK_MAX_LOG_DAYS=14        # keep 2 weeks
AIRLOCK_MAX_LOG_SIZE_MB=200    # rotate at 200MB
```

Old logs are cleaned at startup. Restart the proxy to trigger cleanup immediately.

### Memory usage growing

LiteLLM holds model routing state in memory. With many models and active health checks, memory can grow.

**Check:**
```bash
# In k8s
kubectl top pod -l app=airlock

# Docker
docker stats airlock
```

**Mitigation:** Set memory limits in your deployment (k8s: 1Gi, docker: `--memory 1g`). The HPA manifest scales horizontally when CPU exceeds 70%.

## Docker Issues

### "Broken symlink at .venv/bin/python3"

The `.venv` was created with a Python version that's no longer installed.

**Fix:**
```bash
rm -rf .venv
uv venv .venv --python 3.10
source .venv/bin/activate
pip install -e ".[tui]"
```

### Container health check fails

The health check runs `curl -f http://localhost:4000/health/liveliness` (the
lightweight probe that makes no model calls).

**Check:**
- Is `curl` installed in the image? (It is in `python:3.12-slim`)
- Is the proxy listening? Check container logs: `docker logs airlock`
- Is the port mapping correct? `AIRLOCK_PORT` must match the container port

## Debugging

### Enable verbose logging

```bash
LITELLM_LOG=DEBUG airlock start
```

### Run POST checks

```bash
airlock post              # full diagnostic
airlock post --json       # machine-readable
```

POST checks validate: configuration, provider connectivity, storage backends, guardrail initialization, and MCP server availability.

### Inspect a specific request

```bash
# Find by request_id
grep "REQUEST_ID" logs/airlock-*.jsonl | python -m json.tool

# Find failures
grep '"success": false' logs/airlock-$(date +%Y-%m-%d).jsonl | python -m json.tool
```

### Test guardrails in isolation

```bash
# PII detection
python -c "
from airlock.guardrails.pii_guard import AirlockPIIGuard
guard = AirlockPIIGuard()
print(guard.scan_text('My SSN is 123-45-6789'))
"
```
