# Airlock — Project Progress

## Status: Active Development

Last updated: 2026-02-17

## Completed Work

### PR #1 — Initial Release
Core proxy on LiteLLM with JSONL logging, PII redaction (Presidio), and
keyword blocking. Docker and pip-installable deployment.

### PR #2 — Guardrails Documentation
`dev/` folder with guardrails feature definition and deterministic control
loop design.

### PR #3 — Architecture & Requirements
User needs (UN-1 through UN-9), functional requirements (FR-1 through FR-16),
non-functional requirements (NFR-1 through NFR-10), and architecture doc.

### PR #4 — Dynamic Processing (Fast/Slow)
Real-time threat detection, circuit breaker, priority scoring (fast path).
Offline log analysis, trend detection, hypothesis generation (slow path).

### PR #5 — Development Agents
Agent definitions for development workflow.

### PR #6 — Presidio Research
Investigation into Presidio configuration, stop-word suppression, and
performance tuning.

### Issues #7, #8, #9 — Bug Fixes
- #7: Volume spike heuristic threshold was mathematically unreachable
- #8: OOM kills from Presidio — fixed with shared session fixtures
- #9: Broken pyproject.toml build-backend

### TUI Dashboard (commit ce400c0)
Textual-based terminal dashboard with 6 screens: Dashboard, Models, Threats,
Logs, Analysis, Settings. Includes sidebar navigation and keyboard shortcuts.

### Unified CLI (commits fcfcc06, cd2206a)
`airlock` command with subcommands: init, start, status, tui, analyze.
Pre-flight validation on start, health-check via status.
Requirements FR-17 through FR-23 and NFR-11.

### PR #14 / Issue #12 — Claude Code Hooks
Client-side hooks for Claude Code: SessionStart (proxy health), PreSubmit
(keyword blocking), PreTool (config file protection), PostTool (audit logging).
`airlock hooks install` and `airlock hooks status` commands.

### Issue #13 — Dogfooding (commit 0a643b2)
- Crash-resilient SessionStart hook with recovery guidance
- `dev/dogfooding.md` setup guide
- README Claude Code section expanded with hooks and dogfood commands
- Auth passthrough documented (LiteLLM `os.environ/` syntax)

## Open Issues

### Issue #10 — Basic Keyword Search for TUI Logs
Add free-text keyword search to the Logs screen. Case-insensitive substring
matching against request messages and response content. Wire up `/` keybinding
per TUI design doc. Client-side filtering over loaded records.

### Issue #11 — Hybrid Sparse+Dense Search Backend
Combine BM25 keyword search with embedding-based semantic search for log
retrieval. Reciprocal rank fusion scoring. Local index storage
(SQLite + faiss). Optional `pip install airlock[search]` dependencies.
Depends on #10 completing first.

## Test Suite

- **310 tests** across 23 test files
- **308 passing**, 2 known failures in `test_slow_analyzer` (log file
  path resolution — pre-existing)
- Presidio engines shared via session fixture to avoid OOM
- TUI tests use async `app.run_test()` pattern

## Architecture Summary

| Subsystem | Location | Status |
|-----------|----------|--------|
| Proxy | `airlock/proxy.py` | Complete |
| Guardrails | `airlock/guardrails/` | Complete |
| Callbacks | `airlock/callbacks/` | Complete |
| Fast (real-time) | `airlock/fast/` | Complete |
| Slow (offline) | `airlock/slow/` | Complete |
| Hooks | `airlock/hooks/` | Complete |
| CLI | `airlock/cli/` | Complete |
| TUI | `airlock/tui/` | Complete — search pending (#10) |
