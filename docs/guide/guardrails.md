# Guardrails

Airlock applies a chain of guardrails to every request. Guardrails can observe (log only) or enforce (block) depending on the enforcement mode.

## Guardrail chain

Requests pass through the following guardrail stages in order. The OOM diagnostic
recorder is registered but inert unless explicitly enabled; it does not affect
request policy.

| Stage | Phase | Purpose |
|-------|-------|---------|
| OOM Diagnostic | pre_call, post_call | Opt-in aggregate process/cgroup snapshots for a memory investigation |
| PII Guard | pre_call | Redact credit cards, SSNs, emails, phone numbers (Presidio) |
| Keyword Guard | pre_call | Block requests containing restricted keywords |
| Enhanced Interceptor | pre_call | Inject prompt/parameter defaults for `enhanced/*` model aliases |
| Fast Guardian | pre_call | Threat assessment, circuit breaker check, priority scoring |
| Enforcer | pre_call | Binary blocking gate based on signal scores |
| Local vLLM Router | pre_call | Fail fast when a local-vLLM alias isn't the currently loaded model |
| Semantic Guard | during_call | Prompt-injection classification ([details](prompt-injection.md)) |
| Orchestrator | during_call | Weighted evaluation of all signals |
| MCP Tool Guard | pre_mcp_call | Tool name/argument filtering for MCP calls |
| Response Scanner | post_call | Check response text for PII leaks |
| Reasoning Stripper | post_call | Remove non-standard `◁think▷ … ◁/think▷` blocks for configured models |
| PII Hydrator | post_call | Restore original PII from redaction placeholders |

## Enforcement modes

| Mode | Behavior |
|------|----------|
| `observe` | Log signals only, never block (default) |
| `shadow` | Log what *would* be blocked, but allow the request through |
| `enforce` | Block requests that exceed thresholds |

Set via `AIRLOCK_ENFORCE_MODE` environment variable. Start in `observe` mode, review logs, then promote to `enforce` when confident.

!!! note "The Semantic Guard has its own mode"
    Prompt-injection classification is governed by `AIRLOCK_SEMANTIC_MODE`, not
    `AIRLOCK_ENFORCE_MODE` — the weighted enforcer carries no semantic-classifier
    signal, so the two settings are independent. Both default to `observe`. See
    [Prompt-Injection Detection](prompt-injection.md).

## Per-request guardrail skips

A trusted client (e.g. a benchmark harness) can downgrade specific **content**
guards on its own requests — without a global env flip — by presenting a signed
capability token. This is **off by default**; enable it with
`guardrail_overrides.allow_capability_skip: true`.

Key properties:

- **Skip means downgrade, not silence.** A granted skip lowers a guardrail's
  *effective mode* (typically to `observe`) — the guard still **scans and logs**;
  it just stops blocking. Nothing goes un-audited.
- **Content guards only.** Skips never disable the [circuit breaker](rate-limiting.md)
  or re-enable [fallbacks](routing.md#fallbacks) — provider protection is
  non-skippable.
- **PII is non-skippable by default** (compliance / exfiltration risk).

Configure which guards are skippable, and what each downgrades to, in `config.yaml`:

```yaml
guardrail_overrides:
  allow_capability_skip: false              # master flag — skips ignored until true
  capability_header: X-Airlock-Capability
  skippable:
    pii_redact:      { skippable: false }                  # never, by default
    keyword:         { skippable: true, downgrade_to: observe }
    response_scan:   { skippable: true, downgrade_to: observe }
    reasoning_strip: { skippable: true, downgrade_to: off }
```

### How a client uses it

The operator mints a guardrail-skip token (see [Admin API → minting](admin-api.md#minting-capability-tokens)),
scoped to the specific guard(s):

```bash
# --sub MUST be the client's authenticated key-derived id (key:<last8>).
airlock admin mint-token --sub key:b35cf679 --scope guardrail:skip:keyword --ttl 24h
```

The client then adds **one header** to its requests — alongside, not replacing, its
normal `Authorization` key:

```
POST /v1/chat/completions
Authorization: Bearer <normal-LLM-key>     ← unchanged, the LLM key
X-Airlock-Capability: <skip-scoped-jwt>     ← downgrades the granted guard(s)
```

Only the scopes the token carries (and that config permits) are downgraded; for the
request above, the keyword guard drops to `observe` for that client's requests
only, and everything is still scanned and logged.

> **Security note.** A skip is authorized only when the token's `sub` matches the
> `key:<last8>` derived from the request's *validated* `Authorization` key. The
> forgeable `X-Airlock-Client` attribution header carries **zero** authorization
> weight for skips — a stolen token cannot be replayed by forging it.

## PII redaction, hydration, and egress

Airlock redacts configured PII with Microsoft Presidio before the request
leaves the proxy. The built-in pattern/validation recognizers — `CREDIT_CARD`,
`US_SSN`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `US_BANK_NUMBER`, and `IBAN_CODE` —
use Presidio's no-NLP engine and do **not** load spaCy/Thinc. Adding a semantic
entity such as `PERSON` selects Presidio's normal NLP path, which requires the
configured spaCy model.

The code default contains the first four types; the shipped `.env` template
enables all six. Customize the set with `AIRLOCK_PII_ENTITIES`:

```bash
AIRLOCK_PII_ENTITIES=CREDIT_CARD,US_SSN,EMAIL_ADDRESS
```

PII is replaced with numbered placeholders such as `<EMAIL_ADDRESS_1>`. The
reverse map is kept only in a bounded, process-local store; request metadata
and telemetry carry at most an opaque, short-lived handle, never the original
values. `AIRLOCK_PII_MAP_MAX_ENTRIES` (default `1024`) and
`AIRLOCK_PII_MAP_TTL_SECONDS` (default `300`) bound disconnected or failed
requests.

`AIRLOCK_PII_HYDRATION=tools` (the default) restores placeholders only in
**non-streaming tool-call arguments**. It does not rewrite normal response
prose, and streaming tool-call deltas remain redacted because a placeholder can
span chunks. Set `AIRLOCK_PII_HYDRATION=off` to leave placeholders in tool
arguments.

If redaction is unavailable, `AIRLOCK_PII_FAIL_MODE=open` (default) preserves
the request and records a value-free unavailable marker. Set it to `closed` to
block instead. Hydration always fails closed: an unavailable reverse map leaves
placeholders intact.

### Tool-call egress policy

Before a placeholder is restored into a tool argument, Airlock makes a
value-free decision using the tool name, JSON-pointer-like argument path, and
PII entity class. `AIRLOCK_PII_EGRESS_MODE=observe` is the shipped deployment
posture: it records the decision but permits hydration. `shadow` records what
would be allowed while keeping denied placeholders redacted; `enforce` also
keeps denied placeholders redacted as the final policy posture.

Configure the policy with JSON environment values:

```bash
# classify a tool as a round-trip destination or an external egress destination
AIRLOCK_PII_EGRESS_TOOL_BANDS='{"gmail_search":"round_trip","send_mail":"exfil"}'
# permit a specific class only at a specific egress destination/path
AIRLOCK_PII_EGRESS_ALLOWLIST='[{"tool":"send_mail","path":"/to","entity_type":"EMAIL_ADDRESS"}]'
# always deny a matching tuple (wildcards are supported)
AIRLOCK_PII_EGRESS_BLOCKLIST='[{"tool":"*","path":"*","entity_type":"US_SSN"}]'
```

Unknown tools are denied in `shadow` and `enforce`. Credit-card, SSN, US bank
number, and IBAN classes are denied by default; a blocklist always wins. Egress
telemetry contains policy labels only — never PII values.

## Keyword blocking

Set `AIRLOCK_BLOCKED_KEYWORDS` to a comma-separated list. Case-insensitive substring matching against request content.

```bash
AIRLOCK_BLOCKED_KEYWORDS=project-alpha,internal-codename
```

## Local vLLM router

When several model aliases in `config.yaml` point at the same self-hosted vLLM endpoint (typical when one box swaps between several models), only one is actually loaded at a time. Without this guardrail, calling an unloaded alias surfaces the upstream `the model X does not exist` error with no hint that the box is hosting a *different* model.

The `airlock-local-vllm-router` guardrail (mode: `pre_call`) intercepts these calls and returns a clear, actionable error instead.

How it works:

1. On first call, reads `config.yaml` and treats every `model_list` entry whose `litellm_params.api_base` matches `AIRLOCK_LOCAL_VLLM_BASE_URL` as a local vLLM alias. The expected `served-model-name` is the upstream `model` field with any `openai/` provider prefix stripped.
2. Queries `{base_url}/models` (cached for `AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS`) to discover what vLLM is actually serving.
3. If the requested alias is local and its served-name is loaded → request proceeds. If unloaded → raises with a message naming what *is* loaded and what the user needs to switch to.
4. Cloud and non-local aliases pass through untouched.

Example failure response:

```
Local model 'kimi-dev' (served as 'kimi-dev-72b') is configured but not
currently loaded on http://192.168.1.45:8000/v1. Currently loaded:
qwen3.6-27b. Stop the currently running local vLLM container and start
the one that serves 'kimi-dev-72b' before retrying.
```

Environment variables:

| Variable | Description | Default |
|---|---|---|
| `AIRLOCK_LOCAL_VLLM_BASE_URL` | Local vLLM `/v1` URL to associate aliases with | `http://192.168.1.45:8000/v1` |
| `AIRLOCK_LOCAL_VLLM_CACHE_TTL_SECONDS` | How long to cache the `/models` response | `5` |
| `AIRLOCK_LOCAL_VLLM_SWITCH_HINT` | Optional format string appended to the error. Placeholders: `{requested}`, `{requested_served}`, `{loaded}`, `{loaded_aliases}`, `{base_url}` | (generic message) |

Use the switch hint to embed deployment-specific commands, e.g.:

```bash
AIRLOCK_LOCAL_VLLM_SWITCH_HINT='Run: docker stop <current> && /opt/vllm/start-{requested}.sh'
```

## Reasoning stripper

Some local models emit inline reasoning/thinking blocks that vLLM's native `--reasoning-parser` machinery cannot strip. The reference case is **Kimi-Dev-72B**, which uses `◁think▷ … ◁/think▷` delimiters. These markers are three separate tokens in the Kimi tokenizer (`◁`, `think`, `▷`), so the built-in vLLM parsers — which match on single token IDs — cannot recognize them.

The `airlock-reasoning-stripper` guardrail (mode: `post_call`) does the work at the gateway: scans the response text for the literal Unicode markers and removes the wrapped content, so downstream agents and tool/JSON parsers see only the post-thought output.

- Scoped per-model via `AIRLOCK_REASONING_STRIP_MODELS` (default: `kimi-dev`). Other models pass through untouched. Matches bare aliases (`kimi-dev`) and provider-prefixed forms (`openai/kimi-dev`).
- Handles both non-streaming and streaming response paths. Streaming uses a stateful filter with a small lookbehind buffer so markers split across chunks are still recognized.
- Also strips an orphan trailing `◁/think▷` for the case where the model omits the opening marker.

Environment variables:

| Variable | Description | Default |
|---|---|---|
| `AIRLOCK_REASONING_STRIP_MODELS` | Comma-separated alias list this guardrail applies to | `kimi-dev` |

## Guardrail tuning

The slow analyzer automatically tunes guardrail weights and thresholds based on historical signal data. Tuned values are written to `logs/airlock-knobs.json` and loaded by the orchestrator with a 30-second cache TTL.

## Advisory LLM analysis

`airlock analyze --llm --audience ops|security|executive` keeps the deterministic
report as the fallback. Set `AIRLOCK_ANALYZER_MODEL` to use any LiteLLM-compatible
model, including a local OpenAI-compatible endpoint. Findings are advisory and
never alter knobs or enforcement.

To explicitly opt into the minimized remote Anthropic executor, install
`airlock-llm[analyzer]`, set `AIRLOCK_ANALYZER_REMOTE_SANDBOX=anthropic` and
`AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY=code_execution`, provide
`ANTHROPIC_API_KEY`, and optionally set `AIRLOCK_ANALYZER_REMOTE_MODEL`. The
remote payload contains derived report aggregates only: raw messages, responses,
credentials, and full JSONL records are omitted; textual evidence is redacted
and capped. Without that explicit capability opt-in, credentials, the optional
dependency, or the provider, Airlock returns the deterministic report without
failing.

!!! warning "\"Sandbox\" here names a provider capability, not an Airlock boundary"
    Despite the variable name, **Airlock does not manage, verify, or retrieve
    results from a sandbox.** It sends minimized aggregates to Anthropic's
    Messages API with the server-side `code_execution` tool declared, and reads
    the text answer back. It does not create a sandbox session, upload files to
    one, fetch artifacts from one, or confirm that any code executed.

    So this opt-in controls exactly one thing: **whether derived aggregates
    leave your machine.** It is not a security boundary around analysis, and it
    should not be relied on as one. The variable keeps its name for
    compatibility.

    True provider-sandbox integration — session lifecycle, upload, artifact
    retrieval, attestation — is not implemented and is not planned for 0.5.x.

Run `airlock analyze` to trigger a tuning cycle manually.
