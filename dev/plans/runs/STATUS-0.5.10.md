# STATUS — 0.5.10 (catch-up and clean-up)

_Last updated: 2026-08-05 · package version: **0.5.10** · **released to PyPI**_

## Current state

- **Complete.** All nine packs landed.
- Baseline was `bfc42a7`; the milestone runs from `20a8440` to the release commit.
- Plan: [`0.5.10-plan.md`](../0.5.10-plan.md).
- **Publication decision: released.** 0.5.10 is the first published release since
  0.5.8, and it carries the internal 0.5.9 train with it — including the breaking
  `GET /health` change, which reaches the public here for the first time.

## Pack scoreboard

| Pack | Title | State | Outcome |
|---|---|---|---|
| A-1 | Issue triage and closure | ✅ | 19 → 14 open. #10/#29/#19 closed with evidence; #18/#16 closed with the divergence stated; #17 re-scoped to F-1 Part B |
| A-2 | Deprecation and dead-code sweep | ✅ | `utcnow()` gone; new `timeutil.py`; `_load_logs` shim deleted; deprecation budget enforced by fixture |
| A-3 | Doc drift from 0.5.9 | ✅ | Mostly already done in `bfc42a7`; archive index was the real drift |
| B-1 | TUI semantic classifier visibility (#33) | ✅ | Semantic tab on Guards, reusing `semantic_report.py` |
| B-2 | TUI provider spend and budget (#23) | ✅ | Mostly shipped already; added utilization % and wired the detail pane |
| B-3 | MCP startup timeout, proxy side (#20) | ✅ | Resolved upstream; pinned by regression test and documented. #20 closed |
| C-1 | Advisory tool loop Part B | ✅ | Parameterized + bounded; truncation now reaches the model |
| C-2 | Auth/authz for paid side services (#21) | ✅ | Per-client allowlist on existing seams; quotas deferred to 0.6.x |
| C-3 | Release, docs, closeout | ✅ | 0.5.10 published; pip-audit suppressions re-checked and retained |

## What the plan got wrong

Recorded because the plan told its reader to verify rather than trust it, and
doing so changed the work three times:

- **A-2 predicted 8 `datetime.utcnow()` sites; there were 3.**
- **B-2 assumed the pack was unbuilt.** Spend/cap, headroom, and quarantine had
  shipped already. The real gaps were the unread `budget_utilization` field and
  a detail pane that fetched a snapshot and discarded it.
- **B-3's premise no longer held.** LiteLLM 1.94.1 already classifies listing
  failures and never returns an empty tool list, and the timeout is configurable.
  The right deliverable was a regression test plus documentation, not code.

## Findings

### The 0.5.9 record was wrong about code-inspection enforcement

Four documents and two code comments stated that code inspection has no
enforcement path. `response_scanner._code_inspection_should_block` reads
`knobs.weights["code_inspection"]` and blocks in `enforce` mode; three tests
pin it. What shipped was the F-3 memo's *recommended design* — knobs-sourced
weight, `0.0` default — not the "out of scope" reversal recorded in its
decision table. The reversal was written down and never applied.

Behavior was never at risk: the default weight is `0.0` and blocking also
requires `AIRLOCK_RESPONSE_SCAN_MODE=enforce`. It mattered because every
document an operator would consult said no such path existed. Corrected in
place for live docs, with dated correction blocks in retained evidence.

### Part A's truncation produced unparseable tool results

Oversized advisory tool results were capped by slicing serialized JSON at a byte
offset — output the model could not parse, with the truncation recorded only in
loop metadata it never sees. Fixed in C-1 by shrinking rows inside an envelope
that states `returned` / `total_available` / `truncated`.

### The obvious deprecation-budget implementation is a silent no-op here

`filterwarnings` matches its module field against a path-derived string, and
this repository's root directory is named `airlock` — so every pattern matching
`airlock/slow/analyzer.py` also matches `.venv/.../site-packages/litellm/*.py`.
Enforcing there would have made third-party deprecations fatal, which the plan
forbids. Caught by probing before committing; replaced with a path-resolving
fixture whose discriminator has its own test.

### A flaky TUI test, pre-existing

`test_overview_served_via_renders_backend_kind` failed roughly one run in three.
`_refresh_state` is `@work(exclusive=True)`, so the test triggering it manually
cancels whatever the refresh timer already started, and `wait_for_complete()`
reports that supersede as a failure. Harness race, not a product defect; 0.5.10
changed no worker or timer code in that screen.

## Carried obligations — final state

- **F-1 Part B** — **discharged** (pack C-1).
- **pip-audit suppressions** — **retained, re-checked 2026-08-05.** Running
  without the ignore flags still reports all three advisories; `litellm[proxy]`
  and `presidio-anonymizer` both still pin `cryptography<49.0` while the fixes
  land in 49.0.0 and 50.0.0. Re-check log added to
  `dev/notes/security-pip-audit-exceptions.md`.
- **F-3** — **re-characterized, not deferred.** The enforcement path is plumbed
  and operator-gated at weight `0.0`. Raising that default still needs its own
  observe window; `resource_access` matches ordinary code-assistance traffic.
- **Phase B indirect injection** — still out of scope; design-first release.

## Verification gates

All nine CI jobs were run locally before pushing, per the 0.5.9 habit. Two
failures were caught that would otherwise have failed the push: `uv lock --check`
(stale after the version bump) and the flaky TUI test above.

1. ✅ `uv lock --check`
2. ✅ `ruff check` + `ruff format --check`
3. ✅ `mypy airlock/fast/`
4. ✅ `pytest -m "not live"` — 3107 passed
5. ✅ `mkdocs build --strict`
6. ✅ documentation contract tests
7. ✅ `pip-audit` with the three documented ignores
8. ✅ `docker build`
9. ✅ Green GitHub CI on the released SHA

## Open for the owner

- **`analyzer_llm.py` defaults its opt-in remote model to `claude-sonnet-4-5`.**
  Current Sonnet is `claude-sonnet-5`. Left unchanged: it is a behavior change on
  a paid path, and the remote executor is doubly opt-in, so it is the owner's
  call rather than a cleanup.
- **Enabling paid-service allowlists in production.** Shipped default-off; which
  clients may reach Tavily, Perplexity, and NewsCatcher is a deployment decision.
