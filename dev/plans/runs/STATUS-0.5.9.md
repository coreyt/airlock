# STATUS — 0.5.9 (internal milestone)

_Last updated: 2026-08-04 · package version retained: 0.5.8_

## Current state

- **In flight:** closeout evidence. All planned implementation is complete.
- **Nothing is pushed.** Seven 0.5.9 commits sit on local `main` only, and no CI
  run has ever executed against any of them.
- **Publication:** prohibited. No PyPI upload, public release, or version bump.
- **Closeout marker:** do not create `milestone/0.5.9-internal-closeout` until
  the independent review, the outstanding second-review findings, and a green
  committed CI run are in hand.
- **Steward handoff:** [`0.5.9-MASTER-HANDOFF.md`](../prompts/0.5.9-MASTER-HANDOFF.md).
  Note it predates the 2026-08-04 work and is stale on the corpus and classifier
  items; this board is authoritative where they disagree.

## Scope scoreboard

| Pack | State | Evidence |
|---|---|---|
| TUI transparency and log navigation | Implemented | `tests/test_0_5_9_features.py` |
| Managed MCP readiness timeout | Implemented | focused MCP/TDD tests |
| Programmatic-tool code inspection | Implemented | safe JSONL propagation tests |
| Adaptive semantic selection | Implemented + benchmarked | `51e2270`, `45aafad`; [design](../../notes/design-prompt-injection-classifier.md); [corpora + results](../../corpora/README.md); [access witness](0.5.9-model-armor-access-witness.md) |
| Advisory LLM analysis | Implemented | tool-loop, minimized remote sandbox, fallback tests |
| Health endpoint alignment | Implemented | `1e4c513` (design/plan), `1bd7bd2`; `tests/test_health_endpoints.py` |
| Documentation CI and Pages | Passed (on `385a110`, now stale) | CI `30779548856`, Pages `30779548846` |
| Independent review | **Not started** | — |
| Final CI and internal marker | **Blocked** | needs push + green CI on the final SHA |

## Completed 2026-08-04

Seven unpushed commits:

| Commit | Work |
|---|---|
| `51e2270` | Pluggable semantic prompt-injection classifiers — provider seam, tripwire + Model Armor tiers, `observe`/`shadow`/`enforce` mode, Phase A input boundary |
| `9ae1d8f` | Benchmark harness + deepset corpus evidence |
| `267b25e` | jailbreak-classification corpus; v1-vs-v3 decision; rate-limit hazard |
| `3ca0c4f` | Local operational corpus dropped (owner decision); observe window becomes the gate |
| `45aafad` | Provider rate ceiling + configurable unavailability policy |
| `1e4c513` | Health endpoint design + plan |
| `1bd7bd2` | Health endpoints implemented (**breaking**: `GET /health` no longer calls models) |

Suite at `1bd7bd2`: **2953 passed, 107 skipped, 1 xpassed**. Strict MkDocs build
clean. Ruff clean.

## Production posture

The proxy was restarted 2026-08-04 09:58 CDT and is healthy (81 models, MCP
servers intact, live `claude-haiku`/`claude-sonnet` verified).

**Model Armor is disabled in production** (owner decision): no production prompt
text leaves the machine. Only the local tripwire is registered, in `observe`
mode. Consequence — a production observe window can produce **tripwire**
false-positive evidence only; there is no local evidence path for the semantic
tier, since the local corpus was also declined. Semantic-tier evidence is
benchmark-only, and `shadow`/`enforce` are therefore not on the table for this
deployment without a separate non-production instance.

Unrelated standing issue: the local vLLM host `192.168.1.45:8000` is unreachable,
so `qwen3-32b`, `qwen3.6-27b`, `gemma-4`, `kimi-dev`, and `vllm/*` fail. Not an
Airlock defect.

## Remaining gates

1. **Independent review — not started.** The owner approved a bounded,
   separate-provider automated review after implementation. Packet: tracked
   adapter/guard/extraction sources, focused tests, the design memo, and the
   corpus schema. Excludes credentials, local config, operational logs, and
   unredacted samples. Retain findings, fixes, and re-review.
2. **Four second-review findings still open** (from
   [`0.5.9-verification.md`](../../notes/0.5.9-verification.md)): advisory LLM
   analysis is not a real bounded client-side tool loop; the Anthropic path is a
   minimized Messages API executor rather than provider-sandbox integration;
   code-inspection weights are not connected to post-response enforcement; log
   loading and blocked-client aggregation need bounded-query semantics. Each
   must be implemented or explicitly re-scoped by the owner.
3. **JSONL aggregation tooling for `airlock_semantic` verdicts** — not built.
   Needed to review observe-window output at all.
4. **Push and verify CI.** Re-run full non-live and package checks, push, and
   confirm a green GitHub CI run on that exact SHA.
5. **Then** create `milestone/0.5.9-internal-closeout`.

## Constraints

- Adaptive selection remains opt-in (`AIRLOCK_SEMANTIC_SELECTION=adaptive`).
- Semantic enforcement stays in `observe`; promotion requires evidence this
  deployment cannot currently produce.
- Remote LLM analysis requires explicit `AIRLOCK_ANALYZER_REMOTE_SANDBOX=anthropic`
  and `AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY=code_execution`; it only
  receives minimized derived aggregates.
- ~~Probe `/health/liveliness`, never `/health`.~~ **Retired 2026-08-04.** No
  health endpoint makes model calls, and a test enforces it. Use `/livez` and
  `/readyz`.
