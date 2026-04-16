## Implement Payload-JSON Fathom Storage Model

### Summary
Implement Fathom storage per `dev/design-fathom-storage-model.md`, with `RequestLog` split into first-class event fields plus one optional `payload_json`, write-time `cost`, UTC-only timestamps, read-time rollups for now, and no env-flag compatibility layer. Use TDD throughout. Run work in orchestrated packs via `dev/agent-harness-runbook.md`: preflight, permission canary, one implementation canary, then phased worktree agents with merge gates.

### Public Interfaces and Data Contract
- Add canonical config surface under `fathom:`:
  - `event_fields`: CSV allowlist of Type A first-class fields
  - `payload_fields`: `off` or CSV allowlist of Type B payload members
- Do not keep `AIRLOCK_FATHOM_STORE_*` env flags. Remove writer/reader/tests/docs that rely on them.
- `RequestLog` first-class fields are exactly:
  - `timestamp`, `success`, `error_flag`, `model`, `airlock_provider`, `request_id`, `call_id`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost`, `duration_ms`, `failure_category`, `call_type`, `mcp_tool_name`, `mcp_server_name`, `airlock_client`, `error_type`, `user`, `team`
- `RequestLog.payload_json` members are exactly:
  - `messages`, `response_text`, `headers`, `response`, `guardrail_metadata`, `gemini_metadata`, `mcp_metadata`, `routing_metadata`, `mcp_arguments`
- No mixed schema. Stop writing top-level `messages_json`, `headers_json`, `mcp_arguments_json`.
- Cost source in v1:
  - weekly-refreshed pricing snapshot sourced from LiteLLM `model_prices_and_context_window.json`
  - cached in state dir, last good snapshot kept in memory
  - bundled fallback snapshot in repo for cold start/offline
  - lookup always uses UTC request timestamp
- TUI checkbox controls are not in this implementation. Ship config-first. Keep TUI controls on roadmap only.

### Implementation Changes
- **Phase 0: Orchestrator setup**
  - Run `./scripts/preflight.sh`
  - Launch permission canary per runbook
  - Launch one implementation canary before parallel packs
  - No direct edits on `main`; all code packs in worktrees; merge only after target tests pass
- **Phase 1: Config and contract pack**
  - Add `fathom` config parsing and validation
  - Define internal field-selection model for event fields and payload fields
  - Default selection:
    - `event_fields`: full Type A set
    - `payload_fields`: `off`
  - Add tests for CSV parsing, `off`, unknown field rejection, and defaults
- **Phase 2: Fathom writer pack**
  - Refactor Fathom logger to build one canonical event record from enterprise logger output
  - Materialize only allowed first-class fields onto `RequestLog`
  - Build one `payload_json` object containing only enabled payload members
  - Keep enhanced inner-call skip and call-id dedupe behavior unchanged
  - Ensure raw error body is not separate first-class field; if present, it only lives inside payload member `response`
- **Phase 3: Pricing pack**
  - Add pricing snapshot service with:
    - bundled snapshot file
    - state-dir cache file
    - in-memory active snapshot
    - weekly refresh policy
    - stale-cache fallback to last good snapshot
  - Replace direct `response_cost` use in Fathom writer with pricing service lookup using provider/model/tokens/timestamp
  - Keep enough token/provider facts on event row to permit future repricing
- **Phase 4: Reader alignment pack**
  - Update advisor and billing reads to use first-class `RequestLog` fields only
  - `get_recent_errors()` aggregates from `success`, `error_flag`, `error_type`, `airlock_client`, `model`
  - If payload is off, recent-sample raw error text may be blank; no reader should depend on payload fields for core aggregation
  - Keep MTD/YTD and other rollups as read-time sums for now
- **Phase 5: Docs pack**
  - Align user docs and design note to actual config names, payload model, pricing snapshot source, config-only rollout, and roadmap status

### TDD and Agent Packs
- **Pack A: Config + validator**
  - Own config parsing/validation only
  - Red tests first for defaults, CSV allowlists, `off`, bad field names
- **Pack B: Fathom writer**
  - Own writer schema only
  - Red tests first for:
    - default payload off
    - payload_json exact shape when enabled
    - no legacy `*_json` top-level fields
    - first-class field allowlist enforcement
    - enhanced alias no-duplicate logging
- **Pack C: Pricing**
  - Own pricing service and cost computation only
  - Red tests first for:
    - bundled snapshot load
    - cache refresh
    - last-good fallback
    - UTC timestamp lookup
    - cost correctness for known model/tokens/date
- **Pack D: Readers**
  - Own `airlock.api.queries` and advisor reads only
  - Red tests first for:
    - aggregates from first-class fields
    - no dependency on payload for core counts
    - MTD/YTD sums from stored `cost`
- **Pack E: Docs**
  - Own docs only after code lands
- Phase order:
  - A canary first
  - B and C after A
  - D after B and C
  - E last

### Test Plan
- Unit tests:
  - config parsing/validation for `fathom.event_fields` and `fathom.payload_fields`
  - payload builder returns exact nested keys and omits disabled members
  - legacy env-flag tests removed or rewritten to config-based tests
  - pricing snapshot refresh, cache, fallback, UTC lookup
  - advisor and billing reads over canonical `RequestLog`
- Integration tests:
  - live logical request writes one `RequestLog`
  - enhanced alias still writes one logical row
  - payload off by default yields no `payload_json`
  - selective payload enable writes only requested members
  - cost present on each record and MTD/YTD still sum correctly
- Acceptance scenarios:
  - `payload_fields: off` → only Type A fields stored
  - `payload_fields: messages,response_text` → only those keys under `payload_json`
  - first-class field removed from allowlist → not written, readers tolerate absence
  - failure aggregation works from first-class fields with payload disabled
  - all timestamps and pricing lookups operate in UTC

### Assumptions and Defaults
- `payload_fields` default is `off`
- `event_fields` default is full Type A set
- TUI controls deferred; config is only control surface in this implementation
- Summary tables and aggregate nodes remain roadmap only
- LiteLLM pricing manifest is source of truth for v1 weekly pricing updates; `pricepertoken.com` is not integrated in v1
- No backwards-compat env shim for `AIRLOCK_FATHOM_STORE_*`
