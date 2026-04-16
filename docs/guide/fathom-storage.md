# Fathom Storage

Airlock can optionally write a compact operational record for each logical
request into FathomDB. This page describes the intended data model, what is
stored as first-class fields, what belongs in the nested payload JSON, what is
computed at write time, and what is expected to be derived later from
projections or summary tables.

## Overview

Airlock's Fathom model has four storage shapes:

1. **Event record** — one `RequestLog` node per logical Airlock request
2. **Payload JSON** — optional nested request/response detail attached to that event
3. **Per-event derived fields** — values computed once at write time
4. **Summaries / projections** — aggregates derived from many events

The main design rule is simple:

- Keep stable operational query keys as first-class fields
- Keep bulky or nested data in one payload JSON object
- Compute deterministic per-request values once
- Keep rollups separate from raw event records

## Type A: Event Record

Every logical request may produce one `RequestLog` node in FathomDB.

These fields are intended to remain first-class fields on the event record:

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

Why these stay first-class:

- They are the main operational filter and grouping dimensions
- They support dashboards, billing views, advisor queries, and troubleshooting
- They should not require JSON-path extraction for common queries

## Type B: `payload_json`

Airlock also supports a nested payload JSON object for richer request detail.
This is where variable-shape, bulky, or more privacy-sensitive fields belong.

The intended payload members are:

- `messages`
- `response_text`
- `headers`
- `response`
- `guardrail_metadata`
- `gemini_metadata`
- `mcp_metadata`
- `routing_metadata`
- `mcp_arguments`

This replaces the idea of many parallel JSON-string fields such as
`messages_json`, `headers_json`, and `mcp_arguments_json`.

Instead of many separate JSON blobs, Airlock should prefer one nested payload:

```json
{
  "messages": [...],
  "response_text": "ok",
  "headers": {"x-request-id": "..."},
  "response": {...},
  "guardrail_metadata": {...},
  "gemini_metadata": {...},
  "mcp_metadata": {...},
  "routing_metadata": {...},
  "mcp_arguments": {...}
}
```

### Payload controls

Payload capture is intended to be adjustable per field.

Operationally, this means:

- a global `off` mode is supported
- individual payload members can be enabled or disabled independently
- the TUI can expose these toggles as checkboxes
- configuration can represent the selected payload fields as CSV

The goal is to let operators choose a smaller privacy footprint by default,
while still enabling richer drill-down when needed.

## Type C: Per-Event Derived Fields

Some fields are not raw request inputs, but they are still request-local and
deterministic enough to compute once and store on the event record.

Current example:

- `cost`

Airlock's current direction is:

- maintain a provider/model pricing table
- refresh it on an interval, likely weekly
- keep the newest pricing recordset in memory
- compute event-level cost at write time using the request timestamp and the
  pricing snapshot in effect for that request

This gives fast request writes and stable per-record billing data without
requiring repeated recomputation in the TUI.

## Type D: Summaries and Projections

Multi-request rollups should not be stored on every `RequestLog` event.

Examples:

- MTD / YTD totals
- per-client rollups
- error aggregates

Near-term plan:

- compute these from raw events or projections when queried

Roadmap:

- add dedicated aggregate tables or summary nodes if read frequency or data
  volume makes on-demand summing too slow

This keeps the raw event record simple and prevents mixing facts with
pre-aggregated views.

## UTC Semantics

All stored and computed times are in UTC.

That applies to:

- event timestamps
- pricing lookups for per-record cost
- rollup windows and summary calculations

Timezones are only applied at display time, if a client or UI needs them.

## Configuration Direction

The current intended operator model is:

- **Event field allowlist**: configurable as CSV
- **Payload field allowlist**: configurable as CSV, with `off` supported
- **TUI controls**: checkboxes to enable or disable fields without editing raw YAML

Example direction:

```yaml
fathom:
  event_fields: timestamp,success,error_flag,model,airlock_provider,request_id,call_id,prompt_tokens,completion_tokens,total_tokens,cost,duration_ms,failure_category,call_type,mcp_tool_name,mcp_server_name,airlock_client,error_type,user,team
  payload_fields: off
```

Or:

```yaml
fathom:
  payload_fields: messages,response_text,headers,response,guardrail_metadata,gemini_metadata,mcp_metadata,routing_metadata,mcp_arguments
```

This page documents the user-facing model and intended controls. The exact
config key names and TUI controls may evolve as implementation catches up.

## Current Status

Today, Airlock already writes a compact Fathom record for each logical request
when Fathom logging is enabled, and it already uses Fathom for some billing and
advisor read paths.

The richer model described here is the direction for making Fathom a stronger
operational store:

- compact event fields for common queries
- optional nested payload JSON for rich inspection
- per-record derived cost
- projections now, aggregate tables later
