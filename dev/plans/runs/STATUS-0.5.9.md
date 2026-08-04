# STATUS — 0.5.9 (internal milestone)

_Last updated: 2026-08-04 · package version retained: 0.5.8_

## Current state

- **All planned implementation is complete, pushed, and CI-green.**
- `fcc7a9a` is on `origin/main`. CI run
  [`30931046440`](https://github.com/coreyt/airlock/actions/runs/30931046440)
  passed all five jobs — test (3.12), docs, security, docker, lint — and the
  Pages publish workflow succeeded.
- **Publication remains prohibited.** No PyPI upload, no public release, no
  version bump. The package stays at 0.5.8.
- **Closeout complete.** The observe-window gate was re-scoped by the owner on
  2026-08-04 (Option 1); see [Observe-window gate](#observe-window-gate--re-scoped-2026-08-04-owner).
- The [steward handoff](../prompts/0.5.9-MASTER-HANDOFF.md) predates the
  2026-08-04 work and is stale on the corpus, classifier, and health items.
  **This board is authoritative where they disagree.**

## Scope scoreboard

| Pack | State | Evidence |
|---|---|---|
| TUI transparency and log navigation | Implemented | `tests/test_0_5_9_features.py` |
| Managed MCP readiness timeout | Implemented | focused MCP/TDD tests |
| Programmatic-tool code inspection | Implemented (observational only) | safe JSONL propagation tests |
| Adaptive semantic selection | Implemented + benchmarked | [design](../../notes/design-prompt-injection-classifier.md), [corpora](../../corpora/README.md), [access witness](0.5.9-model-armor-access-witness.md) |
| Advisory LLM analysis | Implemented; tool loop bounded | `ToolLoopBudget` / `ToolLoopOutcome` |
| Health endpoint alignment | Implemented (**breaking**) | [design](../../notes/design-health-endpoints.md), `tests/test_health_endpoints.py` |
| Closeout findings F-1…F-4 | Dispositioned | [design](../../notes/design-0.5.9-closeout-findings.md), [record](../../notes/0.5.9-verification.md) |
| Independent review | **Complete** | [review](0.5.9-independent-review-2026-08-04.md) + [transcripts](0.5.9-independent-review-transcript.md) |
| Observe-window tooling | Implemented | `airlock semantic-report` |
| Final CI | **Green** | run `30931046440` on `fcc7a9a` |
| Internal closeout marker | **Created** | `milestone/0.5.9-internal-closeout` |

## Delivered 2026-08-04

Sixteen commits, `385a110..fcc7a9a`:

| Commit | Work |
|---|---|
| `51e2270` | Pluggable semantic prompt-injection classifiers — provider seam, tripwire + Model Armor tiers, `observe`/`shadow`/`enforce`, Phase A input boundary |
| `9ae1d8f` | Benchmark harness + deepset corpus evidence |
| `267b25e` | jailbreak-classification corpus; v1-vs-v3 decision; rate-limit hazard |
| `3ca0c4f` | Local operational corpus dropped (owner); observe window becomes the gate |
| `45aafad` | Provider rate ceiling + configurable unavailability policy |
| `1e4c513` | Health endpoint design + plan |
| `1bd7bd2` | Health endpoints (**breaking**: `GET /health` no longer calls models) |
| `3dcb179` | Status refresh |
| `e009276` | Independent-review fixes: sanitized classifier errors, bounded provider fan-out |
| `8bafcd3` / `4dd3893` | Closeout-findings design + owner decisions |
| `e8762b7` | Bounded log queries, tool-loop budgets, over-promises removed |
| `fcc7a9a` | `airlock semantic-report`; documented pip-audit exceptions |

Suite: **3009 passed, 107 deselected, 1 xpassed**. Ruff, mypy (fast), strict
MkDocs, documentation contract, Docker build, and pip-audit all pass — every CI
job was also run locally before the push.

## Evidence retained

- **Independent review** (`gpt-5-codex` through the Airlock proxy): nine
  findings — six fixed, two accepted by design, all verified across three
  passes. It confirmed the central invariant: no path turns a provider "no
  verdict" into "clean". Its highest-value finding was a real privacy defect —
  `str(exc)` from classifier failures reaching metadata and logs — which an
  existing test had been *pinning in place*.
- **Benchmarks**: deepset (662 rows) and jailbreak-classification (1,306 rows),
  both filter versions, complete paced runs. v3 selected: recall 0.748 vs 0.781,
  but **1 false positive vs 18**.
- **Model Armor access witness**: the `INSPECT_ONLY` template returning HTTP 200
  with no verdict — the reproduction behind the unavailable-is-never-clean rule.
- **pip-audit exceptions**: three unreachable `cryptography` advisories, scoped
  and documented with a removal trigger.

## Production posture

Restarted 2026-08-04 09:58 CDT; healthy (81 models, MCP intact). **Model Armor
is disabled in production** by owner decision — no production prompt text leaves
the machine. Only the local tripwire is registered, in `observe` mode.

`airlock semantic-report` over live traffic already shows the tripwire firing on
security-related prose — a real false positive of the quoted-benign class the
benchmarks predicted.

Unrelated standing issue: local vLLM host `192.168.1.45:8000` is unreachable, so
`qwen3-32b`, `qwen3.6-27b`, `gemma-4`, `kimi-dev`, and `vllm/*` fail. Not an
Airlock defect.

## Observe-window gate — re-scoped 2026-08-04 (owner)

The gate introduced in `3ca0c4f` made a production observe window the sole
source of local false-positive evidence, replacing the declined local corpus.
Model Armor was then disabled in production, so such a window yields **tripwire
evidence only** and there is no local evidence path for the semantic tier.

**Decision: Option 1 — re-scope the gate to the local tripwire.**

What this accepts, stated plainly so it is not rediscovered later:

- **Model Armor ships as a benchmark-validated capability this deployment does
  not enable.** Its evidence is the public-corpus benchmarks
  (`dev/corpora/README.md`), not local traffic. That is sufficient to *ship* the
  integration; it is not sufficient to *enforce* with it.
- **`shadow` and `enforce` are out of scope for this deployment.** Promoting
  either would require local false-positive evidence that cannot be gathered
  while the semantic tier is disabled here. Anyone enabling Model Armor
  elsewhere inherits the original obligation: observe first, then decide.
- **The tripwire runs in `observe` and its evidence is reviewable** with
  `airlock semantic-report`. That window is no longer a closeout gate, but the
  tool exists and the data accrues, so the evidence is there when wanted.

The gate is therefore satisfied for the tier that is running, and explicitly
waived — not quietly dropped — for the tier that is not.

## Closeout

All preconditions met: implementation complete, independent review retained and
its findings resolved, benchmark evidence retained, all four second-review
findings dispositioned, final commit pushed, and CI green.

`milestone/0.5.9-internal-closeout` marks the closeout commit.

**Correction on the way in:** the marker already existed locally, pointing at
`5003a6d` (2026-08-02) — a commit predating the classifier, health-endpoint,
independent-review, and closeout-findings work. It was created ahead of its
preconditions, contrary to the handoff, and had never been pushed. It was moved
to the true closeout commit. Any reference to the old target is wrong.

Publication remains prohibited: no PyPI upload, no version bump, no `v*` tag.
The release workflow triggers only on `v*`, so this marker cannot publish.

## Constraints

- Adaptive selection remains opt-in (`AIRLOCK_SEMANTIC_SELECTION=adaptive`).
- Semantic enforcement stays in `observe`.
- Remote LLM analysis requires explicit `AIRLOCK_ANALYZER_REMOTE_SANDBOX=anthropic`
  and `AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY=code_execution`; it receives
  minimized derived aggregates only, and is **not** a security boundary.
- ~~Probe `/health/liveliness`, never `/health`.~~ **Retired 2026-08-04.** No
  health endpoint makes model calls, and a test enforces it. Use `/livez` and
  `/readyz`.
