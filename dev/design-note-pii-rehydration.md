# Design Note: PII Hydration For Tool Calls

## Origin

This design note refines the observation in
`memex/observations/Airlock-PII-Hydration-For-Tool-Calls.md` after assessing
it against Airlock's actual codebase. The observation's high-level approach is
sound. This note resolves open questions, specifies integration points, and
identifies where the existing code already provides the building blocks.

---

## Problem

Airlock redacts PII in outbound prompts using Presidio. When the upstream model
returns tool calls whose arguments reference the redacted values, the client
receives unusable placeholders instead of the original user-supplied data.

Example:

```
User input:         "search gmail for emails from coreyt@gmail.com"
After PII guard:    "search gmail for emails from <EMAIL_ADDRESS>"
Model tool call:    gmail_search(from_address="<EMAIL_ADDRESS>")
Client receives:    from_address="<EMAIL_ADDRESS>"  ← broken
```

The privacy boundary works. The problem is that redaction is currently
irreversible, and tool calls are the one structured path where the client
needs the original value to execute correctly.

---

## Why It Is Feasible Now

Five architectural properties of Airlock make this implementable without new
infrastructure:

1. **Metadata passthrough.** Guardrails store per-request state in
   `data["metadata"]` during pre-call. This dict survives into post-call hooks
   via the same `data` reference (non-streaming) or `request_data`
   (streaming). Observer, enforcer, and response scanner all use this pattern
   today.

2. **Post-call hook support.** `CustomGuardrail` supports
   `GuardrailEventHooks.post_call`. The response scanner already registers for
   it and demonstrates both non-streaming
   (`async_post_call_success_hook`) and streaming
   (`async_post_call_streaming_iterator_hook`) paths.

3. **Tool-call access in responses.** The response scanner's
   `_extract_response_text` already walks
   `response.choices[].message.tool_calls[].function.{name, arguments}`.
   The structure is known and accessible.

4. **Recursive JSON traversal.** `_scrub_value_recursive` in `pii_guard.py`
   already handles nested dicts, lists, and strings with a depth guard. The
   hydration walker is the same shape — replace in the opposite direction.

5. **Presidio analyzer results.** `analyzer.analyze()` returns
   `AnalyzerResult` objects with `entity_type`, `start`, `end`, and `score`.
   The original text can be sliced from the input using these spans. The
   current code discards this after `anonymizer.anonymize()` — capturing it
   is the key change.

---

## Design

### 1. Unique Numbered Placeholders

Current Presidio default: `<EMAIL_ADDRESS>` for every email, regardless of
count. This is ambiguous when multiple values of the same type appear.

Change: use Presidio's custom operator support to emit `<EMAIL_ADDRESS_1>`,
`<EMAIL_ADDRESS_2>`, etc., with a per-request counter keyed by entity type.

Implementation approach — **manual replacement, not Presidio's anonymizer:**

```python
def _scrub_text_with_mapping(text: str, mapping: dict[str, str]) -> str:
    analyzer, _ = _get_presidio()
    entities = _configured_entities()
    results = analyzer.analyze(text=text, entities=entities, language="en")
    if not results:
        return text

    # Sort by start offset descending so replacements don't shift positions
    results.sort(key=lambda r: r.start, reverse=True)

    counters: dict[str, int] = {}  # per entity_type
    for result in results:
        original = text[result.start:result.end]

        # Deduplicate: same original value gets the same placeholder
        existing = next(
            (ph for ph, orig in mapping.items() if orig == original),
            None,
        )
        if existing:
            placeholder = existing
        else:
            entity_type = result.entity_type
            counters[entity_type] = counters.get(entity_type, 0) + 1
            placeholder = f"<{entity_type}_{counters[entity_type]}>"
            mapping[placeholder] = original

        text = text[:result.start] + placeholder + text[result.end:]

    return text
```

The `mapping` dict is passed in by reference and accumulates across all
messages and MCP arguments in a single request. This ensures numbering is
globally consistent within the request scope.

**Why not use Presidio's AnonymizerEngine with custom operators?**
Presidio operators are stateless per-call. Maintaining a counter across
multiple `anonymize()` calls (one per message, one per MCP argument field)
requires external state anyway. Performing replacement manually from the
analyzer results is simpler and avoids coupling to Presidio's operator
lifecycle. The analyzer is still doing the heavy lifting (NER + pattern
detection).

### 2. Mapping Storage

The reverse mapping lives in request metadata:

```python
data.setdefault("metadata", {})["airlock_pii_map"] = mapping
```

Properties:
- **Request-scoped.** Tied to the `data` dict for exactly one LiteLLM call.
- **No TTL needed.** The dict is garbage-collected when the request completes.
- **No external store.** Metadata passthrough is in-memory, same-process.
- **No cross-process concern.** Airlock runs LiteLLM in a single process;
  pre-call and post-call execute in the same event loop for a given request.

### 3. Pre-Call Hook Changes

`async_pre_call_hook` changes from fire-and-forget scrubbing to
mapping-aware scrubbing:

```python
async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
    mapping: dict[str, str] = {}

    if is_mcp_call(data, call_type):
        _scrub_mcp_arguments_with_mapping(data, mapping)

    messages = data.get("messages")
    if messages:
        data["messages"] = _scrub_messages_with_mapping(messages, mapping)

    if mapping:
        data.setdefault("metadata", {})["airlock_pii_map"] = mapping
        logger.info(
            "pii_redacted count=%d entity_types=%s",
            len(mapping),
            list({k.rsplit("_", 1)[0].strip("<>") for k in mapping}),
        )

    return data
```

### 4. Post-Call Hook — Non-Streaming

Register for `GuardrailEventHooks.post_call`. Implement
`async_post_call_success_hook` following the response scanner's pattern:

```python
async def async_post_call_success_hook(self, data, user_api_key_dict, response):
    mapping = data.get("metadata", {}).get("airlock_pii_map")
    if not mapping:
        return response

    hydrated_count = _hydrate_tool_calls(response, mapping)

    if hydrated_count:
        logger.info("pii_hydrated count=%d", hydrated_count)

    return response
```

### 5. Tool-Call Hydration

```python
def _hydrate_tool_calls(response: Any, mapping: dict[str, str]) -> int:
    """Replace PII placeholders in tool-call arguments. Returns count."""
    count = 0
    if not response or not hasattr(response, "choices"):
        return 0
    for choice in response.choices:
        msg = getattr(choice, "message", None)
        if not msg:
            continue
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            if not fn:
                continue
            args_str = getattr(fn, "arguments", None)
            if not args_str:
                continue
            try:
                args = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                logger.warning("pii_hydration_skip reason=malformed_json")
                continue
            args, n = _hydrate_value_recursive(args, mapping)
            count += n
            fn.arguments = json.dumps(args)
    return count
```

### 6. Recursive Hydration Walker

Mirror of `_scrub_value_recursive`, replacing placeholders instead of PII:

```python
def _hydrate_value_recursive(
    value: Any, mapping: dict[str, str], _depth: int = 0,
) -> tuple[Any, int]:
    """Replace known placeholders in a JSON-decoded value. Returns (value, count)."""
    if _depth >= 20:
        return value, 0
    if isinstance(value, str):
        count = 0
        for placeholder, original in mapping.items():
            if placeholder in value:
                value = value.replace(placeholder, original)
                count += 1
        return value, count
    elif isinstance(value, dict):
        total = 0
        for k, v in value.items():
            value[k], n = _hydrate_value_recursive(v, mapping, _depth + 1)
            total += n
        return value, total
    elif isinstance(value, list):
        total = 0
        for i, item in enumerate(value):
            value[i], n = _hydrate_value_recursive(item, mapping, _depth + 1)
            total += n
        return value, total
    return value, 0
```

### 7. Streaming Path

The streaming post-call hook yields chunks as they arrive, then acts after
the stream completes. Tool calls in streaming responses arrive as deltas that
the client assembles. Hydrating individual deltas is fragile — a placeholder
token may span chunk boundaries.

**Recommended approach for v1: defer streaming hydration.**

Rationale:
- Claude Code (the primary Airlock client) uses non-streaming for tool calls.
- Streaming tool-call hydration requires buffering and reassembly, which adds
  complexity and latency.
- The non-streaming path covers the correctness gap described in the problem
  statement.

If streaming hydration is needed later, the approach would be: accumulate
tool-call argument deltas (same as response scanner does for text), hydrate
the assembled JSON, then emit a corrective final chunk.

### 8. What Is NOT Hydrated

- **Assistant prose.** Free-form text stays redacted. Placeholders in prose
  are not semantically structured enough for safe replacement.
- **Tool names.** Only argument values are hydrated.
- **Argument keys.** Only values within the JSON are walked.

Optional future expansion via `AIRLOCK_PII_RESPONSE_HYDRATION`:
- `tools` (default) — hydrate tool-call arguments only
- `text_and_tools` — also hydrate assistant content (higher risk)
- `off` — disable hydration entirely, redact-only mode

### 9. Logging

Pre-call log line (existing, adjusted):
```
pii_redacted count=2 entity_types=['EMAIL_ADDRESS']
```

Post-call log line (new):
```
pii_hydrated count=1
```

Neither line emits raw original values.

### 10. Failure Modes

| Condition | Behavior |
|---|---|
| No mapping in metadata | Return response unchanged, no log |
| Empty mapping dict | Return response unchanged |
| Unknown placeholder in args | Left as-is (only known keys replaced) |
| Malformed arguments JSON | Skip that tool call, log warning |
| Depth > 20 | Stop recursion, same as scrub path |
| Presidio unavailable | No redaction, no mapping, no hydration |

### 11. Guardrail Execution Order

The PII guard must run **before** other pre-call guards (so they see redacted
text) and **after** the response scanner in post-call (so the scanner sees
the unhydrated response for injection detection — placeholders are fine for
pattern matching).

Current order in `config.yaml` already places PII guard first in pre-call.
For post-call, adding `post_call` to the PII guard's event hooks registers it
alongside the response scanner. LiteLLM dispatches post-call hooks in
registration order. The PII guard should be registered **after** the response
scanner in the guardrails list so hydration runs last.

If that is not sufficient, post-call ordering can be controlled by having the
PII guard check for the response scanner's metadata before running — but this
is unlikely to be needed.

### 12. Security Boundary

| Direction | Content | Status |
|---|---|---|
| Outbound to provider | Messages, MCP arguments | Redacted (unchanged) |
| Inbound to client — tool arguments | Structured JSON fields | Hydrated (new) |
| Inbound to client — assistant prose | Free-form text | Redacted (unchanged) |
| Logs | Entity types, counts | No raw values (unchanged) |
| Metadata (internal) | Placeholder-to-original map | Ephemeral, request-scoped |

The hydration does not introduce new data. It restores the client's own
input in a structured action path. The provider never sees original values.

---

## Compatibility

- **Client API contract unchanged.** Responses are standard `ModelResponse`
  objects. Tool-call argument payloads are corrected, not restructured.
- **Existing tests unaffected.** Current redaction tests verify outbound
  scrubbing. They continue to pass because the pre-call path still redacts.
  New tests cover the round trip.
- **Config backward compatible.** No new config keys required. The
  `AIRLOCK_PII_HYDRATION` env var defaults to `tools` (enabled). Setting it
  to `off` restores current behavior.

---

## What Changes Per File

| File | Change |
|---|---|
| `airlock/guardrails/pii_guard.py` | New `_scrub_text_with_mapping`, post-call hook, `_hydrate_tool_calls`, `_hydrate_value_recursive`. Remove bare `_scrub_text` calls from public path (keep as internal fallback). Add `post_call` to event hooks. |
| `config.yaml` | Move PII guard entry after response scanner in guardrails list (if ordering matters for post-call dispatch). |
| `tests/test_pii_guard.py` | New test classes for mapping, hydration, round trip, edge cases. |
| `tests/harness/test_phase2_pii.py` | Integration tests for full redact-hydrate cycle. |

No other files need modification.
