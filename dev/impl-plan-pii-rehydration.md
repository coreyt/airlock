# Implementation Plan: PII Hydration For Tool Calls

Reference: `dev/design-note-pii-rehydration.md`, `dev/test-plan-pii-rehydration.md`

---

## Phase 1 — Numbered Placeholders and Mapping Capture

**File:** `airlock/guardrails/pii_guard.py`

### Step 1.1: Add `_scrub_text_with_mapping`

New function replacing direct `_scrub_text` usage in the request path. Uses
`analyzer.analyze()` to get entity spans, then performs manual replacement
with numbered placeholders while building a reverse mapping dict.

Key behaviors:
- Counter per entity type: `<EMAIL_ADDRESS_1>`, `<EMAIL_ADDRESS_2>`, etc.
- Deduplication: same original value reuses the same placeholder.
- Sort results by `start` descending so string slicing doesn't shift offsets.
- Mapping dict is passed by reference, accumulates across calls within one
  request.

Keep `_scrub_text` as-is for backward compatibility / fallback if Presidio
custom operators are ever needed.

### Step 1.2: Add mapping-aware message and MCP scrubbers

- `_scrub_messages_with_mapping(messages, mapping)` — same structure as
  `_scrub_messages` but calls `_scrub_text_with_mapping` and threads `mapping`
  through.
- `_scrub_mcp_arguments_with_mapping(data, mapping)` — same as
  `_scrub_mcp_arguments` but threads `mapping` through.
- `_scrub_value_recursive_with_mapping(value, mapping, _depth)` — same
  recursion as `_scrub_value_recursive` but calls
  `_scrub_text_with_mapping`.

### Step 1.3: Update `async_pre_call_hook`

- Create empty `mapping: dict[str, str] = {}`.
- Call the `_with_mapping` variants instead of the originals.
- After scrubbing, if `mapping` is non-empty, attach it:
  `data.setdefault("metadata", {})["airlock_pii_map"] = mapping`.
- Log entity types and count (no raw values).

### Step 1.4: Tests for Phase 1

Add to `tests/test_pii_guard.py`:

- `TestScrubTextWithMapping` — test classes A1–A4 from test plan.
- `TestMappingStorage` — test classes B1–B5.
- Verify existing `TestScrubText` and `TestScrubMessages` still pass
  (backward compat of `_scrub_text`).

Run: `uv run python -m pytest tests/test_pii_guard.py -v`

---

## Phase 2 — Post-Call Hydration (Non-Streaming)

**File:** `airlock/guardrails/pii_guard.py`

### Step 2.1: Register for post-call events

Change `supported_event_hooks` in `__init__`:

```python
supported_event_hooks = [
    GuardrailEventHooks.pre_call,
    GuardrailEventHooks.pre_mcp_call,
    GuardrailEventHooks.post_call,
]
```

### Step 2.2: Add `_hydrate_value_recursive`

New function: walks a JSON-decoded value (dict/list/str), replaces known
placeholder strings with originals from the mapping. Returns
`(value, count)`. Depth guard at 20, same as scrub path.

### Step 2.3: Add `_hydrate_tool_calls`

New function: iterates `response.choices[].message.tool_calls[].function`,
parses `.arguments` as JSON, runs `_hydrate_value_recursive`, writes back
with `json.dumps`. Handles malformed JSON gracefully (skip + warn).

### Step 2.4: Implement `async_post_call_success_hook`

```python
async def async_post_call_success_hook(self, data, user_api_key_dict, response):
    mapping = data.get("metadata", {}).get("airlock_pii_map")
    if not mapping:
        return response
    if not _hydration_enabled():
        return response
    count = _hydrate_tool_calls(response, mapping)
    if count:
        logger.info("pii_hydrated count=%d", count)
    return response
```

### Step 2.5: Add `_hydration_enabled` helper

Reads `AIRLOCK_PII_HYDRATION` env var. Values: `tools` (default), `off`.
Returns `True` unless explicitly `off`.

### Step 2.6: Tests for Phase 2

Add to `tests/test_pii_guard.py`:

- `TestHydrateValueRecursive` — unit tests for the walker (C3–C6, I3–I5).
- `TestHydrateToolCalls` — tests with `make_response` / `make_tool_call`
  fixtures (C1, C2, C7–C10).
- `TestPostCallSuccessHook` — integration through the hook method (C1, C8,
  E1–E4).
- `TestHydrationConfig` — H1, H2.

Fixtures needed:
- `make_response(content=None, tool_calls=None)` — builds a
  `SimpleNamespace` mimicking `ModelResponse`.
- `make_tool_call(name, arguments_dict)` — builds a `SimpleNamespace` with
  `.function.{name, arguments}`.

Run: `uv run python -m pytest tests/test_pii_guard.py -v`

---

## Phase 3 — Failure Modes and Edge Cases

**File:** `airlock/guardrails/pii_guard.py`, `tests/test_pii_guard.py`

### Step 3.1: Malformed JSON handling

In `_hydrate_tool_calls`, wrap `json.loads` in try/except. On
`JSONDecodeError` or `TypeError`, log warning and skip that tool call.

### Step 3.2: Edge case tests

Add tests from plan sections E and I:
- E1: no mapping → pass-through.
- E2: unknown placeholder → unchanged.
- E3: malformed JSON → skip + warn.
- E4: empty mapping → pass-through.
- I1–I7: empty messages, empty args, depth limit, concurrent requests,
  Presidio unavailable.

Run: `uv run python -m pytest tests/test_pii_guard.py -v`

---

## Phase 4 — Round-Trip Integration Tests

**File:** `tests/harness/test_phase2_pii.py`

### Step 4.1: Add round-trip test class

`TestPIIHydrationRoundTrip` — tests G1–G4 from test plan. Each test:
1. Calls `async_pre_call_hook` with PII-containing data.
2. Asserts outbound messages are redacted.
3. Constructs a mock response with tool calls containing the placeholders
   from step 1.
4. Calls `async_post_call_success_hook` with the response + original data.
5. Asserts tool-call arguments contain the original PII values.

### Step 4.2: Privacy boundary tests

`TestPIIPrivacyBoundary` — tests F1–F3. Verify outbound stays redacted, logs
don't contain raw values.

Run: `uv run python -m pytest tests/harness/test_phase2_pii.py -v`

---

## Phase 5 — Streaming Path (Deferred)

Not implemented in v1. Document the approach in a code comment:

```python
# Streaming hydration deferred — tool-call deltas may split placeholders
# across chunks. If needed: accumulate function.arguments deltas, hydrate
# assembled JSON, emit corrective chunk. See design-note-pii-rehydration.md.
```

If the primary client (Claude Code) starts using streaming for tool calls,
revisit.

---

## Phase 6 — Config and Guardrail Ordering

**File:** `config.yaml` (if needed)

### Step 6.1: Verify post-call execution order

The response scanner should scan before PII hydration runs (it checks for
injection patterns; placeholders are fine for that). Verify that the PII
guard's `post_call` hook runs after the response scanner's. If LiteLLM
dispatches post-call hooks in guardrail list order, move PII guard after
response scanner in `config.yaml`.

If ordering is not configurable at the config level, add a note and verify
empirically.

### Step 6.2: Env var documentation

Add `AIRLOCK_PII_HYDRATION` to the env var reference in the project (wherever
`AIRLOCK_PII_ENTITIES` and `AIRLOCK_RESPONSE_SCAN_MODE` are documented).

---

## Execution Order Summary

| Phase | Scope | Risk | Estimated Size |
|---|---|---|---|
| 1 | Numbered placeholders + mapping | Low — additive, no behavior change for callers | ~80 lines code, ~80 lines tests |
| 2 | Post-call hydration | Medium — new response mutation path | ~60 lines code, ~120 lines tests |
| 3 | Failure modes | Low — defensive code + tests | ~20 lines code, ~80 lines tests |
| 4 | Integration tests | Low — test-only | ~100 lines tests |
| 5 | Streaming | Deferred | Comment only |
| 6 | Config / ordering | Low — config adjustment | Minimal |

Total new code: ~160 lines in `pii_guard.py`.
Total new tests: ~380 lines across test files.

---

## Validation Checkpoints

After each phase, run:

```bash
# Unit tests
uv run python -m pytest tests/test_pii_guard.py -v

# Integration tests (after Phase 4)
uv run python -m pytest tests/harness/test_phase2_pii.py -v

# Full suite regression
uv run python -m pytest tests/ -q --ignore=tests/harness -k "not test_status_def"
```

---

## Dependencies

- No new packages. Presidio `AnalyzerEngine` is already a dependency.
  `AnonymizerEngine` is no longer called in the hot path (manual replacement
  from analyzer results), but remains available as fallback.
- `json` stdlib module (add import to `pii_guard.py`).
