# Design Note: Airlock Fathom Storage Model

**Date:** April 15, 2026  
**Status:** Proposed  
**Author:** Codex  
**Context:** Airlock operational storage, advisor reads, TUI billing, future projections

---

## 1. Executive Summary

Airlock currently writes compact `RequestLog` rows into FathomDB, but schema is
too narrow for advisor/error analysis and too ad hoc for future TUI and billing
work. This note defines target Fathom storage model with four distinct shape
types:

1. `RequestLog` event record
2. `payload_json` nested request/response detail
3. per-event derived fields
4. summary/projection records

Core rule:

- stable operational query keys stay first-class fields
- bulky or variable-shape detail moves into one nested JSON payload
- deterministic request-local values compute once at write
- rollups stay out of raw event rows

This gives Airlock:

- better queryability than opaque JSONL blobs
- better privacy controls than always-on full payload capture
- clean path to summary tables later
- compatibility with FathomDB 0.4.5 JSON-path filters and property-path FTS

---

## 2. Goals

- Make Fathom `RequestLog` rows useful for advisor, TUI, billing, debugging
- Separate raw facts from derived facts from aggregates
- Keep privacy-sensitive payload capture adjustable per field
- Keep common queries fast without forcing JSON-path extraction everywhere
- Support future FTS/index registration over selected nested JSON paths
- Keep all storage/computation timestamps in UTC

## 3. Non-Goals

- Build aggregate tables in first phase
- Encode every possible LiteLLM callback field in first phase
- Replace JSONL immediately as full audit trail
- Auto-index every nested payload path in Fathom

---

## 4. Shape Types

### 4.1 Type A: Event Record

One `RequestLog` node per logical Airlock request.

These fields stay first-class event properties:

- `timestamp`
- `success`
- `error_flag`
- `model`
- `airlock_provider`
- `request_id`
- `call_id`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `cost`
- `duration_ms`
- `failure_category`
- `call_type`
- `mcp_tool_name`
- `mcp_server_name`
- `airlock_client`
- `error_type`
- `user`
- `team`

Why first-class:

- common filters
- grouping dimensions
- billing and error analysis
- TUI/Advisor operational queries
- avoid JSON extraction for normal use

### 4.2 Type B: `payload_json`

One nested payload object attached to `RequestLog`.

Payload members:

- `messages`
- `response_text`
- `headers`
- `response`
- `guardrail_metadata`
- `gemini_metadata`
- `mcp_metadata`
- `routing_metadata`
- `mcp_arguments`

This replaces parallel `messages_json`, `headers_json`,
`mcp_arguments_json`-style top-level blobs.

Why one payload object:

- less schema sprawl
- easier privacy policy
- easier versioning
- maps naturally to Fathom JSON-path filters
- allows selective path indexing later

### 4.3 Type C: Per-Event Derived Fields

Values computed once for request, stored on that request.

Current required field:

- `cost`

Possible future fields:

- normalized provider family
- normalized failure class
- pricing version id

### 4.4 Type D: Summary / Projection Records

Records derived from many `RequestLog` rows.

Examples:

- MTD/YTD totals
- per-client rollups
- per-model error aggregates
- per-provider billing summaries

These do **not** belong on raw event rows.

---

## 5. Compute Timing

### 5.1 At Write Time

Store:

- Type A event fields
- enabled `payload_json` members
- Type C derived fields

This includes:

- `cost`

### 5.2 At Read Time

Compute on demand for now:

- MTD totals
- YTD totals
- per-client rollups
- error aggregates

This is acceptable until data volume or latency says otherwise.

### 5.3 Background / Projection Phase

Later, add projection jobs or summary-upsert writes for:

- `ClientCostSummary`
- `ProviderCostSummary`
- `ModelErrorSummary`
- other heavy repeated rollups

Roadmap item. Not phase 1.

---

## 6. Cost Policy

Decision:

- maintain provider/model pricing table
- refresh on interval, likely weekly
- keep newest pricing snapshot in memory
- compute event-level `cost` at write time
- use request timestamp + active pricing snapshot for date-correct cost

Benefits:

- fast billing reads
- no repeated multiplication in TUI
- stable per-record billing facts

Constraints:

- pricing snapshots must be versioned enough to explain later values
- repricing old requests requires raw token facts to remain stored

Recommended addition:

- store `pricing_snapshot_id` later if cost auditability becomes important

---

## 7. Config Surface

Two independent field controls.

### 7.1 Event Field Allowlist

CSV-backed config for Type A fields.

Example:

```yaml
fathom:
  event_fields: timestamp,success,error_flag,model,airlock_provider,request_id,call_id,prompt_tokens,completion_tokens,total_tokens,cost,duration_ms,failure_category,call_type,mcp_tool_name,mcp_server_name,airlock_client,error_type,user,team
```

### 7.2 Payload Field Allowlist

CSV-backed config for Type B members, plus `off`.

Examples:

```yaml
fathom:
  payload_fields: off
```

```yaml
fathom:
  payload_fields: messages,response_text,headers,response,guardrail_metadata,gemini_metadata,mcp_metadata,routing_metadata,mcp_arguments
```

### 7.3 TUI Controls

TUI should expose:

- checkbox list for event fields
- checkbox list for payload fields
- top-level `payload off` switch

TUI edits same config surface. No hidden alternate state.

---

## 8. UTC Rule

All stored and computed times are UTC.

Applies to:

- `timestamp`
- pricing lookup for `cost`
- billing windows
- summary/projection windows

Timezone conversion happens only at display time, if caller/UI asks.

---

## 9. FathomDB 0.4.5 Fit

Local FathomDB 0.4.5 supports:

- JSON-like `properties`
- JSON-path filters over nested fields
- property-path FTS schema registration

Implication:

- Airlock can keep scalar operational fields first-class
- Airlock can store nested payload in `payload_json`
- Airlock can later register selected payload paths for filter/FTS support

Important constraint:

- do not assume arbitrary nested payload is automatically indexed
- register only high-value paths

Likely future index candidates:

- `$.payload_json.routing_metadata.final_model`
- `$.payload_json.gemini_metadata.mode`
- `$.payload_json.mcp_metadata.server_name`
- selected text paths for FTS, not whole payload

---

## 10. RequestLog Contract

### 10.1 First-Class Fields

Required contract for `RequestLog`:

| Field | Type | Source | Default |
|---|---|---|---|
| `timestamp` | ISO8601 UTC string | write time | required |
| `success` | bool | callback outcome | required |
| `error_flag` | bool | callback outcome | required |
| `model` | string | request/final model | required |
| `airlock_provider` | string | inferred/provider metadata | optional |
| `request_id` | string | `litellm_call_id` | required |
| `call_id` | string | `litellm_call_id` | required |
| `prompt_tokens` | int | usage | `0` |
| `completion_tokens` | int | usage | `0` |
| `total_tokens` | int | usage | `0` |
| `cost` | float | write-time compute | `0` |
| `duration_ms` | int | callback timing | optional |
| `failure_category` | string | normalized failure logic | optional |
| `call_type` | string | request metadata | optional |
| `mcp_tool_name` | string | request metadata | optional |
| `mcp_server_name` | string | request metadata | optional |
| `airlock_client` | string | request metadata | optional |
| `error_type` | string | normalized exception type | optional |
| `user` | string | metadata | optional |
| `team` | string | metadata | optional |

### 10.2 Nested Payload

`payload_json` shape:

```json
{
  "messages": [],
  "response_text": "",
  "headers": {},
  "response": {},
  "guardrail_metadata": {},
  "gemini_metadata": {},
  "mcp_metadata": {},
  "routing_metadata": {},
  "mcp_arguments": {}
}
```

Members present only when enabled.

---

## 11. Summary Record Roadmap

Later summary record shapes:

### 11.1 `ClientCostSummary`

- `period_type`
- `period_start`
- `client_id`
- `request_count`
- `error_count`
- `total_cost`
- `total_tokens`

### 11.2 `ProviderCostSummary`

- `period_type`
- `period_start`
- `provider`
- `request_count`
- `total_cost`

### 11.3 `ModelErrorSummary`

- `period_type`
- `period_start`
- `model`
- `error_type`
- `count`

These stay roadmap until real read pressure proves need.

---

## 12. Migration Plan

### Phase 1

- stabilize Type A field contract
- collapse blob fields toward one `payload_json`
- keep write-time `cost`
- keep read-time aggregate computation
- document config controls

### Phase 2

- move field control from env flags to config/TUI
- add explicit payload allowlist/off support
- align advisor reads to canonical `RequestLog` contract

### Phase 3

- add pricing snapshot updater
- add projection/summary nodes
- register selected Fathom JSON paths for query/FTS acceleration

---

## 13. Open Questions

- Should `payload_json` itself be first-class name, or should nested members
  live directly under `properties`?
- Should `user` / `team` ship enabled by default, or require policy opt-in?
- Do we need `pricing_snapshot_id` immediately, or only when repricing/audit
  becomes requirement?
- Which payload paths deserve FTS/index registration first?

---

## 14. Recommendation

Adopt this model:

- Type A first-class event fields for operational queries
- Type B single nested `payload_json` for rich detail
- Type C write-time `cost`
- Type D later projections / aggregate tables

This is cleanest path from current compact Fathom ledger toward durable
operational store without turning every request row into opaque audit blob.
