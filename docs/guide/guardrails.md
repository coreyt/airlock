# Guardrails

Airlock applies a chain of guardrails to every request. Guardrails can observe (log only) or enforce (block) depending on the enforcement mode.

## Guardrail chain

Requests pass through 9 stages in order:

| Stage | Phase | Purpose |
|-------|-------|---------|
| PII Guard | pre_call | Redact credit cards, SSNs, emails, phone numbers (Presidio) |
| Keyword Guard | pre_call | Block requests containing restricted keywords |
| Fast Guardian | pre_call | Threat assessment, circuit breaker check, priority scoring |
| Enforcer | pre_call | Binary blocking gate based on signal scores |
| Semantic Guard | during_call | LLM-based content classification |
| Orchestrator | during_call | Weighted evaluation of all signals |
| MCP Tool Guard | pre_mcp_call | Tool name/argument filtering for MCP calls |
| Response Scanner | post_call | Check response text for PII leaks |
| PII Hydrator | post_call | Restore original PII from redaction placeholders |

## Enforcement modes

| Mode | Behavior |
|------|----------|
| `observe` | Log signals only, never block (default) |
| `enforce` | Block requests that exceed thresholds |

Set via `AIRLOCK_ENFORCE_MODE` environment variable. Start in `observe` mode, review logs, then promote to `enforce` when confident.

## PII redaction

Uses Microsoft Presidio with the `en_core_web_lg` spaCy model.

Default entities: `CREDIT_CARD`, `US_SSN`, `EMAIL_ADDRESS`, `PHONE_NUMBER`.

Customize with `AIRLOCK_PII_ENTITIES`:

```bash
AIRLOCK_PII_ENTITIES=CREDIT_CARD,US_SSN,EMAIL_ADDRESS
```

PII is redacted with placeholders (`[EMAIL_ADDRESS_1]`) before the request leaves the network. The PII Hydrator restores original values in the response so the client receives correct data.

## Keyword blocking

Set `AIRLOCK_BLOCKED_KEYWORDS` to a comma-separated list. Case-insensitive substring matching against request content.

```bash
AIRLOCK_BLOCKED_KEYWORDS=project-alpha,internal-codename
```

## Guardrail tuning

The slow analyzer automatically tunes guardrail weights and thresholds based on historical signal data. Tuned values are written to `logs/airlock-knobs.json` and loaded by the orchestrator with a 30-second cache TTL.

Run `airlock analyze` to trigger a tuning cycle manually.
