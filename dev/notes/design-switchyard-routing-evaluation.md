# Evaluation: NVIDIA NeMo Switchyard as an Airlock 0.6.0 routing input

**Date:** 2026-08-28
**Status:** Research finding. Hypothesis stated, tested against Switchyard source
and a working build. **Not accepted scope.** No dependency, endpoint, config key,
or behavior change is authorized by this note.
**Scope of investigation (read-only):** `airlock/fast/router.py`,
`airlock/fast/guardian.py`, `config.yaml` guardrail order, `docs/guide/routing.md`;
NVIDIA-NeMo/Switchyard @ `463a62c` (2026-08-28) and `nemo-switchyard` 0.2.0 on PyPI.
**Evidence:** measured locally; commands and numbers in §5.

---

## 1. Why this was looked at

0.6.0 has no plan file yet. Its only recorded commitments are the B5
identity/keystore foundation and the Slice 60 virtual-key management deferred
onto it (`dev/plans/0.5.15/README.md`, `dev/plans/0.5.15-todo.md`), plus the
0.6.0 removal of the deprecated capability-suffix model twins
(`dev/user-needs.md` UN-24). Cost routing is untouched by that list, and it is
the one shipped Airlock subsystem with **no measured quality evidence at all**:
`tests/test_fast_router.py` has 98 behavioral tests and not one accuracy or
regression measurement of the `smart` classifier's tier choices.

NVIDIA released NeMo Switchyard (Apache-2.0) as an open-source model router
built for exactly Airlock's traffic — coding agents making long tool-using runs.
That makes it worth asking whether Airlock should keep growing its own
classifier or take an input from Switchyard.

## 2. What Switchyard actually is

A Rust LLM proxy plus an embeddable routing library:

- `switchyard-server` — standalone proxy, TOML deployment, serves OpenAI Chat,
  OpenAI Responses, and Anthropic Messages, translating between them.
- `libsy` — the routing engine. Algorithms: `random`, `stage_router`,
  `llm_classifier` (capability / escalation / custom schema), `composite`,
  `advisor`.
- Python bindings (`nemo-switchyard`, maturin/pyo3) exposing `switchyard.libsy`
  and an embeddable `switchyard_rust.server.Server`.

The two algorithm families differ in a way that decides everything below:

| Family | Needs an extra model call? | Signal |
|---|---|---|
| `stage_router` | **No** | Tool-call/tool-result history already on the request |
| `llm_classifier` | **Yes** — one judge call per decision | Prompt content, judged by a model |

`stage_router` scores the conversation's tool history on two corroborative axes —
error `severity`, `spinning`, `exploring` push toward the capable tier;
`recent_production_intensity` (writes/edits landing) pushes toward the efficient
tier — `tanh`-squashes the signed score to a confidence, and acts only above
`confidence_threshold`. Its tool-name and error tables
(`crates/libsy/src/algorithms/util/tool_signals.rs`) are tuned for
`Read`/`Edit`/`Write`/`Bash`/`TodoWrite` and Python tracebacks: Claude Code's
vocabulary, which is Airlock's dogfooding client.

NVIDIA ships its own LiteLLM integration (`examples/litellm/`), and its
compatibility table is the most useful single fact in the repo: **a LiteLLM
routing plugin can host `random` and `stage_router` but must reject
`llm_classifier` and escalation routes**, because a routing plugin cannot
service an intermediate model call. Airlock is not subject to that limit —
Airlock's routing runs inside its own `pre_call` guardrail
(`airlock/fast/guardian.py:484`), where it may `await` anything and still owns
dispatch.

## 3. Hypothesis

> **H.** Airlock's `smart` router is blind to agent trajectory: it classifies
> only the concatenated `role == "user"` text of a request
> (`airlock/fast/router.py:_extract_text`), so every turn of one coding-agent run
> classifies identically, and the tier is then pinned by session affinity.
> Switchyard's `stage_router` reads the signal Airlock discards — the tool-result
> history — and can be run **decision-only, in-process, with no model call and no
> network I/O**. Therefore Airlock can consume it as a second routing input at
> the existing `apply_routing` seam without ceding dispatch, budget enforcement,
> circuit breaking, or guardrail order, and without violating the hot-path
> invariant.
>
> **H2 (corollary).** Because Airlock services model calls itself, Airlock can
> also host `llm_classifier` — routing the judge call back through its own
> LiteLLM, guardrails, and spend ledger — which NVIDIA's own LiteLLM plugin
> cannot do.

## 4. What the tests were

Built the Python extension from Switchyard `463a62c`
(`maturin build --release`, Rust 1.96.1, 2m18s) and drove `libsy` directly from
Airlock's own interpreter, comparing it against `classify_complexity` on
identical inputs.

- **T1** — same agent run, three turns: does either router distinguish them?
- **T2/T5** — does the normalized request tolerate the payloads Airlock forwards?
- **T3** — is `llm_classifier` decision-only, and who issues the judge call?
- **T4** — steady-state latency vs conversation length.
- **T6** — feasibility spike: a real OpenAI-wire → normalized adapter, end to end.

## 5. Results

### T1 — the blindness is confirmed and it is worse than "blind"

One run of a debugging task; the user's opening text never changes.

| Turn | Switchyard `stage_router` | Model calls | Airlock `smart` |
|---|---|---|---|
| 1 — opening task, no history | `claude-opus` (capable) | 0 | `simple` / **low**, score 0.124 |
| 8 — two tracebacks, reads, TodoWrite | `claude-opus` (capable) | 0 | `simple` / **low**, score 0.124 |
| 20 — edits landing, tests passing | `claude-haiku` (efficient) | 0 | `simple` / **low**, score 0.124 |

Airlock returns a byte-identical verdict for all three turns. Two separate
causes, both in `_extract_text`:

1. it reads only `role == "user"` messages, so assistant tool calls are invisible; and
2. within a user message it takes only `type == "text"` blocks, so the
   `tool_result` blocks that carry Anthropic-normalized tool output are dropped
   too — the tool history is discarded twice over.

The classifier is not merely trajectory-blind. On this task it scores **0.124 →
`simple` → tier `low`**, so a multi-file debugging run routes to `claude-haiku`
and, via session affinity, stays there. A short, plainly-worded hard task is
exactly the case the six weighted features under-score.

### T3 — `llm_classifier` costs one judge call, and Airlock would own it

`Step.CallModel(models=['judge'], algorithm='llm_task_classifier')`, then
`Step.Done`. The algorithm **requests** the call and hands it back; the host
performs it. Confirms H2: Airlock could satisfy that call through its own
LiteLLM path, so the judge's tokens land in Airlock's ledger, its guardrails,
and its circuit breaker — the thing NVIDIA's routing-plugin integration
structurally cannot do. It also confirms the cost: a full LLM round-trip on the
hot path, per decision (mitigable to once-per-session via `classify_trigger`).

### T4 — latency, and the fast-path budget

`stage_router`, decision-only, warm, 200 iterations:

| History | Messages | p50 | p95 | Airlock `smart` p50 |
|---|---|---|---|---|
| 0 turns | 1 | 192 µs | 433 µs | 13 µs |
| 5 turns | 11 | 261 µs | 377 µs | 14 µs |
| 20 turns | 41 | 592 µs | 815 µs | 14 µs |
| 60 turns | 121 | 909 µs | 1149 µs | 20 µs |
| 150 turns | 301 | 1560 µs | 1891 µs | 30 µs |

It is linear in conversation length and **exceeds the 150 µs budget that
`classify_complexity` documents** — by 1.3× on a fresh request and ~10× on a
long agent run. Against a multi-second upstream call this is noise, but the
comparison that matters is not the upstream call: it is Airlock's async event
loop, where a pyo3 extension holding the GIL for ~1.5 ms per request is
per-request serialization, not free CPU. Architecture invariant 1 —
"no addition may contend with the inference hot path" — is a real gate here, and
this note does not clear it. Measuring it under concurrency is required work,
not a formality.

### T2/T5 — the request shape is not Airlock's, and the adapter is Airlock's to own

Switchyard's normalized `Request` rejects what Airlock forwards every day:

| Payload | Result |
|---|---|
| OpenAI `content: "hello"` (plain string) | `ValueError: invalid type: string, expected internally tagged enum ContentBlock` |
| `role: "function"` (legacy OpenAI) | `ValueError: unknown variant 'function'` |
| image block, empty messages, system-only | accepted, defaults to capable |

The `switchyard-translation` crate does this conversion, but **HEAD exposes only
`libsy` and `server` to Python** — no translation module. So Airlock must write
and maintain the OpenAI/Anthropic-wire → normalized adapter itself, and keep it
correct as Switchyard's pre-1.0 protocol types move.

### T6 — feasibility spike: it works end to end

A ~60-line conservative adapter (drop what is not understood, never guess) over
the payload shape `guardian` actually receives, driving `stage_router`:

| Trajectory | Messages | Switchyard | p50 | p95 | Airlock |
|---|---|---|---|---|---|
| opening | 1 | `claude-opus` | 173 µs | 234 µs | low |
| struggling (tracebacks) | 11 | `claude-opus` | 262 µs | 419 µs | low |
| producing (edits landing) | 11 | `claude-haiku` | 315 µs | 657 µs | low |

Adapter included, a realistic agent turn decides in well under a millisecond and
the tier tracks the trajectory.

### Packaging

| | Published `nemo-switchyard` 0.2.0 | Built from HEAD `463a62c` |
|---|---|---|
| Python floor | **≥ 3.12** — Airlock's floor is 3.10, its venv is 3.11 | ≥ 3.10 (`cp310-abi3`), installs and imports on 3.11 |
| Runtime deps | `openai>=2.7`, `anthropic>=0.99`, `httpx>=0.28.1`, `pydantic>=2.13.3` | **none** |
| Routing API | algorithm owns the call via a host-supplied `LlmClient` | decision-only `run_stream` → `Step.CallModel` / `Step.Done` |

The published wheel is a different generation from HEAD — it still ships a
Python proxy (`switchyard/lib/**`) that HEAD has moved into Rust. Its API would
make the algorithm the caller of the model, which Airlock cannot accept.

**The decision-only API Airlock would need is unreleased.** Consuming it today
means building a Rust extension from a pre-alpha `main`, or vendoring the
decision logic. NVIDIA's own example says as much
(`examples/litellm/README.md`: "the current published release does not contain
the decision-only Python API used here"), and its LiteLLM pin is 1.97.0 against
Airlock's validated 1.94.1.

## 6. Verdict on the hypothesis

**H is supported on mechanism and refuted as a near-term dependency.**

Supported: `stage_router` is genuinely decision-only (0 model calls, measured),
in-process, sub-millisecond on realistic turns, keyed on Claude Code's tool
vocabulary, and it separates turns of one run that Airlock's classifier cannot
tell apart. Nothing about it requires Airlock to give up dispatch, budget,
circuit breaking, or guardrail order. H2 is confirmed and is a genuine
Airlock-only advantage.

Refuted as a dependency, on four independent grounds, any one of which is
disqualifying under this repo's gates:

1. **Unreleased API.** The needed surface exists only on a pre-alpha `main`.
   Vendoring a from-source Rust build into a pip-installable Python package is a
   supply-chain change of a different order than adding a wheel.
2. **Published wheel is unusable.** Python ≥ 3.12 against Airlock's 3.10 floor,
   plus four hard runtime deps overlapping LiteLLM's pins.
3. **Airlock owns the adapter.** No translation binding; a hand-maintained
   normalizer tracks a pre-1.0 protocol.
4. **Hot-path budget unproven.** 10× over the documented budget on long runs,
   GIL-holding, unmeasured under concurrency.

## 7. What is actually worth taking from this

The valuable finding is not the dependency — it is that Airlock has been
measuring the wrong thing, and this is fixable in Airlock's own code.

1. **Fix `_extract_text` first (bug, not a feature).** Dropping `tool_result`
   blocks is a defect in its own right, and it is not confined to the router:
   `airlock/text_extract.py:extract_text_from_messages` and
   `pii_guard.py:_scrub_messages` — which between them feed the threat detector,
   keyword scanning, and PII redaction — take only `type == "text"` blocks from
   list-shaped content by the same rule.

   **This was filed as a lead and has since been verified as a live guardrail
   gap with security impact.** On the `ANTHROPIC_BASE_URL` dogfooding path
   LiteLLM hands the guardrails the raw Anthropic body, where tool output
   arrives as a `tool_result` block nested in a user message: it is not scanned
   and not redacted, and it egresses to the provider. The evidence, requirements,
   and staged fix are in
   [design-conversation-text-extraction.md](design-conversation-text-extraction.md).
   It outranks everything else in this section and is not 0.6.0 roadmap work.
2. **Adopt the signal, not the library.** Tool-history staging — error severity,
   spinning, exploring, production intensity — is a few hundred lines of pure
   Python at Airlock's own latency, no dependency, no protocol coupling. Take the
   idea; the Apache-2.0 source is a readable reference for the tables.
3. **Build the measurement Airlock lacks.** Switchyard's real contribution here
   is method: a benchmark harness (Harbor Terminal-Bench Lite) and a published
   cost/accuracy claim. Airlock ships a cost router with zero routing-quality
   evidence. A replay-based routing-quality oracle over recorded JSONL, in the
   slow path, is worth more than any classifier swap — and it is what would let
   a future Switchyard decision be evaluated rather than argued about. Drafted in
   [design-routing-quality-oracle.md](design-routing-quality-oracle.md); item 2
   is gated on it, because this note shows only that `stage_router` *decides
   differently*, never that it decides *better*.
4. **Keep the seam clean.** If `apply_routing` grows a pluggable tier-advisor
   interface, a Switchyard-backed advisor stays a drop-in later, once the API is
   released and the concurrency measurement exists.
5. **Re-check when Switchyard cuts a release** that publishes the decision-only
   API with a ≤ 3.11 floor and no runtime deps. At that point items 1–3 are done
   and the comparison is honest.

Also worth recording separately: Switchyard serves **native Anthropic Messages**
and its CLI ships a Claude Code launcher. That is adjacent to the deferred
Claude Code subscription-preserving gateway candidate
(`dev/plans/0.5.15-todo.md`), which is a protocol-fidelity problem, not a routing
one. It is a different investigation and is not opened here.

## 8. Reproduction

```bash
git clone --depth 1 https://github.com/NVIDIA-NeMo/Switchyard.git   # 463a62c
cd Switchyard && maturin build --release -o dist                    # ~2m18s, Rust 1.96.1
                                                                    # -> nemo_switchyard-0.2.0-cp310-abi3-manylinux_2_38_x86_64.whl
```

Install into a throwaway 3.11 venv, put Airlock on `sys.path`, and drive
`switchyard.libsy.algorithms.stage_router(...).run_stream(request)` to
`Step.Done`, failing any `Step.CallModel`. Scripts used for T1–T6 were scratch
and are not retained; §5 records every number they produced.
