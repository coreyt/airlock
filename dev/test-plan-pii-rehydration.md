# Test Plan: PII Rehydration For Tool Calls

## Scope

Tests for request-scoped PII placeholder mapping and post-call hydration of
structured tool-call arguments. Covers the full redaction-hydration round trip
through `AirlockPIIGuard`.

All tests live in `tests/test_pii_guard.py` alongside existing PII tests unless
otherwise noted. Harness-level (integration) tests go in
`tests/harness/test_phase2_pii.py`.

---

## Fixtures

| Fixture | Purpose |
|---|---|
| `pii_guard` | `AirlockPIIGuard()` instance |
| `mock_cache` | `DualCache` stub |
| `mock_user_api_key_dict` | API key metadata dict |
| `reset_presidio_singletons` | Session-scoped Presidio engines |
| `presidio_available` | Skip gate |
| `make_response(content, tool_calls)` | Build a `ModelResponse`-like object with `.choices[].message.{content, tool_calls}` |
| `make_tool_call(name, arguments_dict)` | Build a single tool call with `.function.{name, arguments}` where arguments is a JSON string |

---

## A. Unique Placeholder Generation

### A1. Single entity produces numbered placeholder

Input: `"Email me at alice@corp.com"`
Assert: scrubbed text contains `<EMAIL_ADDRESS_1>`, not bare `<EMAIL_ADDRESS>`.

### A2. Two same-type entities get distinct numbers

Input: `"From alice@corp.com to bob@corp.com"`
Assert: scrubbed text contains both `<EMAIL_ADDRESS_1>` and `<EMAIL_ADDRESS_2>`.
Assert: the two placeholders replace different original values.

### A3. Mixed entity types each get their own counter

Input: `"Email alice@corp.com, card 4111111111111111"`
Assert: `<EMAIL_ADDRESS_1>` and `<CREDIT_CARD_1>` both present.

### A4. Same value appearing twice maps to same placeholder

Input: `"From alice@corp.com and again alice@corp.com"`
Assert: both occurrences replaced by the same `<EMAIL_ADDRESS_1>`.
Assert: mapping has exactly one entry for that placeholder.

### A5. Multipart content gets consistent numbering

Input: two text parts in one message, each with a different email.
Assert: `<EMAIL_ADDRESS_1>` in first part, `<EMAIL_ADDRESS_2>` in second.
Assert: single unified mapping covers both.

### A6. MCP arguments get same numbering scheme

Input: `mcp_arguments: {"to": "alice@corp.com", "cc": "bob@corp.com"}`
Assert: distinct numbered placeholders in arguments.
Assert: mapping stored in `data["metadata"]`.

---

## B. Mapping Storage In Request Metadata

### B1. Mapping attached after redaction

Run pre-call hook on a message with PII.
Assert: `data["metadata"]["airlock_pii_map"]` exists.
Assert: it is a `dict[str, str]` mapping placeholder to original.

### B2. Mapping not attached when no PII detected

Run pre-call hook on `"What is the capital of France?"`.
Assert: `"airlock_pii_map"` not in `data.get("metadata", {})`.

### B3. Mapping correct for multiple entities

Input: `"alice@corp.com and 4111111111111111"`
Assert: mapping has two entries with correct placeholder-to-original pairs.

### B4. Mapping preserves existing metadata

Pre-populate `data["metadata"] = {"airlock_other": True}`.
Run pre-call hook with PII.
Assert: both `airlock_other` and `airlock_pii_map` present.

### B5. Mapping available in MCP path

Run pre-call hook with `call_type="call_mcp_tool"` and PII in arguments.
Assert: `data["metadata"]["airlock_pii_map"]` populated.

---

## C. Post-Call Hydration — Non-Streaming

### C1. Single placeholder in tool-call argument hydrated

Pre-call: `"search gmail for alice@corp.com"` (stores mapping).
Response: tool call `gmail_search(from_address="<EMAIL_ADDRESS_1>")`.
Assert: after post-call hook, `from_address` is `"alice@corp.com"`.

### C2. Multiple placeholders in one tool call

Pre-call: message with two emails.
Response: tool call with both placeholders in different argument fields.
Assert: both fields hydrated to their respective originals.

### C3. Placeholder embedded in larger string

Pre-call: message with email.
Response: tool call argument `"from:<EMAIL_ADDRESS_1> newer_than:7d"`.
Assert: hydrated to `"from:alice@corp.com newer_than:7d"`.

### C4. Nested dict arguments

Response tool-call arguments:
```json
{"config": {"recipient": "<EMAIL_ADDRESS_1>"}}
```
Assert: nested value hydrated.

### C5. Nested list arguments

Response tool-call arguments:
```json
{"attendees": [{"email": "<EMAIL_ADDRESS_1>"}, {"email": "<EMAIL_ADDRESS_2>"}]}
```
Assert: both list elements hydrated with correct distinct values.

### C6. Mixed placeholders and literal text

Response: `{"query": "<EMAIL_ADDRESS_1>", "limit": 10, "label": "inbox"}`.
Assert: only the placeholder field is changed; `limit` and `label` untouched.

### C7. Non-tool-call response left unchanged

Pre-call: message with PII.
Response: assistant prose containing `<EMAIL_ADDRESS_1>`.
Assert: prose is NOT hydrated (only tool arguments are).

### C8. Response with no tool calls passes through

Response: plain text content, no tool_calls.
Assert: response returned unmodified, no errors.

### C9. Multiple tool calls in one response

Response: two separate tool calls, each with a placeholder.
Assert: both hydrated independently.

### C10. Tool call with no placeholders in arguments

Response: `gmail_search(query="inbox", limit=10)` — no placeholders.
Assert: arguments unchanged.

---

## D. Post-Call Hydration — Streaming

### D1. Accumulated tool-call text hydrated after stream completes

Simulate streaming chunks that assemble into a tool call with a placeholder.
Assert: after stream completes and post-hook runs, metadata or final response
reflects hydration occurred.

### D2. Streaming prose not hydrated

Stream chunks forming assistant text with `<EMAIL_ADDRESS_1>`.
Assert: yielded chunks are unmodified.

---

## E. Missing / Ambiguous Mapping (Failure Modes)

### E1. No mapping in metadata — placeholders left as-is

Post-call hook receives a response with `<EMAIL_ADDRESS_1>` but
`data["metadata"]` has no `airlock_pii_map`.
Assert: argument value unchanged.
Assert: warning logged.

### E2. Unknown placeholder token — left as-is

Mapping has `<EMAIL_ADDRESS_1>` but response contains `<EMAIL_ADDRESS_99>`.
Assert: `<EMAIL_ADDRESS_99>` left unchanged.

### E3. Malformed tool-call arguments JSON

`function.arguments` is `"not valid json {"`.
Assert: no crash, response returned as-is, warning logged.

### E4. Empty mapping dict

`data["metadata"]["airlock_pii_map"] = {}`.
Response has placeholders.
Assert: placeholders unchanged, no error.

---

## F. Privacy Boundary Preservation

### F1. Outbound request still redacted

After pre-call hook, `data["messages"]` contains only placeholders, not originals.
(This is an existing test — verify it still passes with the new code.)

### F2. Mapping not logged by default

Capture log output during pre-call with PII.
Assert: log lines contain entity types and counts but not raw original values.

### F3. Hydrated values not logged by default

Capture log output during post-call hydration.
Assert: log records hydration count and entity types, not restored values.

---

## G. Round-Trip Integration Tests

### G1. Full round trip — single email

Pre-call with email in message → model returns tool call with placeholder →
post-call hydrates → client receives original email in arguments.

### G2. Full round trip — multiple entity types

Message with email + credit card → model returns two tool calls using both
placeholders → both hydrated correctly.

### G3. Full round trip — MCP path

MCP arguments with PII → redacted → (simulated) MCP response contains
placeholder → hydrated on return.

### G4. Full round trip — no PII

Message with no PII → no mapping created → response passes through unchanged.

---

## H. Configuration / Feature Toggle

### H1. Hydration disabled by env var

Set `AIRLOCK_PII_HYDRATION=off`.
Pre-call still redacts and stores mapping.
Post-call does NOT hydrate — placeholders returned as-is.

### H2. Default is hydration enabled (tools only)

No env var set.
Assert: hydration runs on tool-call arguments.

### H3. Future mode — text and tools

Set `AIRLOCK_PII_RESPONSE_HYDRATION=text_and_tools`.
Assert: assistant prose also hydrated (optional, may be deferred).

---

## I. Edge Cases

### I1. Empty message list

Pre-call with `messages: []`. No crash, no mapping.

### I2. Message with no content field

Pre-call with `[{"role": "system"}]`. No crash, no mapping.

### I3. Tool-call arguments is empty string

`function.arguments = ""`. No crash.

### I4. Tool-call arguments is empty object

`function.arguments = "{}"`. No crash, no changes.

### I5. Deeply nested arguments (depth > 20)

Arguments nested 25 levels deep with a placeholder at level 22.
Assert: placeholder at depth > 20 is NOT hydrated (matches existing depth guard).

### I6. Concurrent requests do not leak mappings

Two simultaneous pre-call hooks with different PII.
Assert: each request's post-call only sees its own mapping
(guaranteed by metadata dict scoping, but worth verifying).

### I7. Presidio unavailable — graceful degradation

Presidio not installed. Pre-call returns data unchanged, no mapping stored.
Post-call has no mapping, passes response through.
