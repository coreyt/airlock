# 0.5.9 — design for the four open closeout findings

**Status:** Proposed — 2026-08-04
**Source:** second independent review, recorded in
[`0.5.9-verification.md`](0.5.9-verification.md)
**Gate:** each finding must be implemented or **explicitly re-scoped by the
owner** before `milestone/0.5.9-internal-closeout`.

The four findings are not equally severe, and treating them as one block would
be a mistake. One is a real robustness defect, one is a small honesty problem in
how a feature is described, and two are unbuilt features whose absence is only a
problem because earlier documents implied they existed. Each section states what
is actually true today, what "resolved" should mean, and a recommendation.

Summary of recommendations:

| # | Finding | Recommendation | Size |
|---|---|---|---|
| F-4 | Unbounded log queries | **Implement in 0.5.9** | S–M |
| F-3 | Code-inspection weights unconnected | **Implement plumbing only**, default off | S |
| F-1 | Advisory tool loop is not "real" | **Implement bounds; defer parameterized querying** | M |
| F-2 | Anthropic path is not sandbox integration | **Re-scope — fix the claim, not the code** | XS |

---

## F-4 — Bounded-query semantics for log loading and aggregation

**Recommendation: implement in 0.5.9.** This is the only finding of the four
that is a live defect rather than an unbuilt feature.

### What is true today

`airlock/tui/screens/logs.py` is already correct: it stops at
`_MAX_LOG_RECORDS = 5_000` while scanning, and both the inner and outer loops
break. That work landed in 0.5.9.

The other readers did not get the same treatment:

- `airlock/slow/analyzer.py::_load_logs(days=7)` reads **every record** from
  every daily file into a list with no cap. On this machine the July 9 file
  alone is 14 MB.
- `airlock/advisor/tools.py::get_recent_errors` calls
  `get_request_logs(engine, limit=1000000)` — a limit in name only. It then
  materializes `[n.properties for n in nodes]` before filtering.
- `get_guard_signals`, `get_client_profile`, and `get_model_profile` share the
  same unbounded `_load_logs` path.

### Why it matters

The advisor and slow analyzer are reachable from the CLI and the TUI. A user
with a year of logs runs `airlock analyze` or asks the advisor a question and
the process attempts to hold every record in memory at once. There is no
degradation path — it either fits or the process dies. Under the systemd unit's
`MemoryMax=4G` that is an OOM kill of the **proxy**, not just the analysis.

This is also the finding most likely to bite in exactly the deployment that
matters most: the one that has been running long enough to accumulate history.

### Design

Introduce one bounded reader that every consumer uses, in a new
`airlock/log_query.py`:

```python
@dataclass(frozen=True)
class LogQuery:
    days: int = 7
    max_records: int = 50_000       # hard ceiling on retained records
    max_bytes: int = 256 * 1024**2  # hard ceiling on bytes scanned
    newest_first: bool = True
    predicate: Callable[[dict], bool] | None = None  # filter while scanning

@dataclass(frozen=True)
class LogPage:
    records: list[dict]
    scanned: int
    truncated: bool          # a limit stopped the scan
    limit_hit: str | None    # "max_records" | "max_bytes" | None
    oldest_seen: str | None
```

Three properties matter:

1. **Filter while scanning, not after.** The predicate runs per line so a
   narrow query never materializes the whole corpus. This is what makes a
   50k-record ceiling generous rather than restrictive.
2. **Truncation is reported, never silent.** `LogPage.truncated` and
   `limit_hit` propagate into analysis output and advisor answers. An analysis
   over a truncated window that presents itself as complete is worse than one
   that refuses — it produces confident wrong conclusions about traffic it
   never saw.
3. **Newest-first scanning.** Days are walked backwards from today so a
   truncated result holds the most recent records, which is what every consumer
   actually wants.

Consumers change to pass a `LogQuery` and to surface `truncated`:

- `slow/analyzer.py::_load_logs` → delegates; the report gains a
  `window_truncated` field.
- `advisor/tools.py` → all four readers delegate; the `limit=1000000` call is
  replaced with a real limit, and tool results carry `truncated` so the advisor
  can say "based on the most recent N records" instead of implying totality.
- `tui/screens/logs.py` → keeps its 5,000 view cap but adopts the shared reader
  so the semantics live in one place.

Environment overrides: `AIRLOCK_LOG_QUERY_MAX_RECORDS`,
`AIRLOCK_LOG_QUERY_MAX_BYTES`.

### Tests

- A synthetic log directory exceeding both ceilings; assert the scan stops, the
  record count is capped, and `truncated`/`limit_hit` are set.
- A predicate matching one record in 10,000; assert only one is retained and
  memory-proportional behavior via `scanned`.
- Newest-first ordering across day boundaries.
- Every consumer surfaces truncation rather than dropping it.
- A malformed-line file still parses the remainder (existing behavior).

---

## F-3 — Code-inspection weights are not connected to enforcement

**Recommendation: implement the plumbing, default weight 0.0.** Do not enable
enforcement in 0.5.9.

### What is true today

`airlock/guardrails/code_inspection.py::inspect_code` returns
`"enforcement_weight": 0.0` as a **hardcoded literal**. Nothing reads it. The
orchestrator's weighted evaluation (`orchestrator.py::_evaluate`) sums
`knobs.weights.get(signal.guardrail_name, 0.0)` over emitted signals, and code
inspection never emits a signal at all.

So the field is decoration: it implies a wiring that does not exist.

### The honest framing

Code inspection is *observational by design* — the module docstring says so, and
0.5.9 shipped it as safe category/count evidence. The review's finding is
therefore not "this is broken" but "a value named `enforcement_weight` that is
always 0.0 and read by nobody is misleading."

There are two defensible resolutions, and the wrong one is to quietly wire
inspection into blocking. Post-response enforcement on generated code is a
behavior change with real false-positive risk: `resource_access` matches any
`open(` or `requests.` in a code block, which is ordinary in the code-assistance
traffic that dominates this deployment. Turning that into blocking without an
observe window would be the same mistake the semantic classifier work
deliberately avoided.

### Design

Emit the signal; leave the weight at zero.

1. `inspect_code` gains a real `enforcement_weight` sourced from knobs rather
   than a literal, defaulting to `0.0`.
2. Code inspection emits a `GuardrailSignal(guardrail_name="code_inspection",
   score=<existing score>)` into the same signal list the orchestrator already
   evaluates, so the composite score *can* include it.
3. Because the default weight is `0.0`, the composite score is **numerically
   unchanged** — `weighted_sum += score * 0.0`, and `total_weight` is unaffected
   by a zero weight. This is verified by test, not assumed.
4. The tuner (`slow/tuner.py`) may propose a non-zero weight from observed data,
   as it already does for other guardrails; proposals remain advisory.

The result: the wiring is real and inspectable, an operator can enable it
deliberately with evidence, and 0.5.9 ships with identical behavior.

### Tests

- Signal is emitted with the observed score and the correct guardrail name.
- With default knobs, composite score and enforcement decisions are **bit-identical**
  to a run without the signal — the test that makes this safe to ship.
- A non-zero configured weight does change the composite (proving the wiring).
- Weight comes from knobs, not from the literal.

---

## F-1 — The advisory LLM path is not a real bounded tool loop

**Recommendation: implement the bounds; defer parameterized querying to 0.5.10.**

### What is true today

`analyzer_llm.py::_run_tool_loop` is a genuine loop — it calls the model, reads
tool calls, appends tool results, and iterates up to `_MAX_TOOL_ROUNDS = 3`. The
review's objection is about substance, and it is fair:

- **The tools take no arguments.** `if parsed not in ({}, None): return None` —
  any argument at all aborts the loop. The four "tools" (`summary`,
  `optimizations`, `semantic_insights`, `hypotheses`) are fixed slices of an
  already-computed payload. The model cannot filter, scope, or drill in.
- **The payload is precomputed in full** before the loop starts, so the tools
  reveal data rather than query it. Nothing is saved by the model not asking.
- **Every deviation returns `None`** — unknown tool, unparseable arguments,
  arguments present, rounds exhausted. All collapse to one silent failure that
  is indistinguishable from "the model had nothing to say", and the caller
  falls back without recording why.
- **The only bound is a round count.** No wall-clock budget, no token budget.
  Three rounds against a slow model is unbounded in the dimension that matters.

### Design

Two parts. Ship the first in 0.5.9; the second is a feature.

**Part A — make the bounds real and the failures legible (0.5.9).**

```python
@dataclass(frozen=True)
class ToolLoopBudget:
    max_rounds: int = 3
    max_seconds: float = 60.0
    max_tool_calls: int = 8       # across all rounds, not per round
    max_result_bytes: int = 64_000  # cap what is fed back per tool result

@dataclass
class ToolLoopOutcome:
    content: str | None
    stop_reason: str   # "completed" | "max_rounds" | "timeout" |
                       # "max_tool_calls" | "disallowed_tool" |
                       # "bad_arguments" | "no_content"
    rounds: int
    tool_calls: int
    elapsed_seconds: float
```

`_run_tool_loop` returns a `ToolLoopOutcome` instead of `str | None`. The
analyzer records `stop_reason` in its report so a fallback is attributable
rather than mysterious. Every early return becomes a named stop reason.

**Part B — parameterized querying (defer to 0.5.10).** Give tools real
arguments (time range, model, client, limit), validated against a strict schema,
served through the F-4 bounded reader. This is what makes it a query loop rather
than a reveal, but it is a feature with its own design surface, and shipping it
in a milestone already carrying a breaking change and a new classifier
subsystem is poor sequencing.

### Tests

- Budget exhaustion for each dimension yields the right `stop_reason`.
- A tool result exceeding `max_result_bytes` is truncated, and truncation is
  reported.
- Disallowed tool name and malformed arguments produce distinct stop reasons
  rather than one silent `None`.
- The fallback path records the stop reason.
- Existing advisory behavior is otherwise unchanged.

---

## F-2 — The Anthropic path is a Messages API executor, not sandbox integration

**Recommendation: re-scope. Correct the description; do not build.**

### What is true today

The optional remote path (`AIRLOCK_ANALYZER_REMOTE_SANDBOX=anthropic` plus
`AIRLOCK_ANALYZER_REMOTE_SANDBOX_CAPABILITY=code_execution`) sends minimized
derived aggregates to Anthropic's Messages API, declaring the
`code_execution_20250825` tool and the `code-execution-2025-08-25` beta header.

The review's point is precise: declaring the tool is not the same as integrating
with a sandbox. Airlock does not manage a sandbox session, does not upload
files to it, does not retrieve artifacts from it, and does not verify that any
code executed. It sends a request and reads a text answer. Whether the provider
runs code server-side is invisible to Airlock and irrelevant to what it does
with the result.

### Why building the real thing is the wrong call here

A genuine provider-sandbox integration means session lifecycle, file upload,
artifact retrieval, and result attestation — a substantial subsystem — in
service of a feature that is **advisory only**, explicitly opt-in, gated behind
an extra, and cannot change enforcement or write knobs. The cost is
disproportionate to the value, and it would expand a milestone that is already
oversized.

The actual defect is a **documentation and naming** problem: the configuration
variable is named `..._REMOTE_SANDBOX`, which promises more than the code
delivers. That is the part worth fixing, because it is the part that could
mislead an operator into believing analysis is sandboxed in some security-
relevant way.

### Design

1. Keep the behavior. Rename the concept in documentation from "sandbox
   integration" to what it is: **a minimized remote analysis executor that
   declares the provider's code-execution tool.**
2. State plainly in `docs/guide/advisor.md` and the design record: Airlock does
   not manage, verify, or retrieve results from a provider sandbox, and the
   opt-in exists to control *whether derived aggregates leave the machine*, not
   to provide an execution boundary.
3. Keep `AIRLOCK_ANALYZER_REMOTE_SANDBOX` as-is for compatibility, documented
   with a note that "sandbox" refers to the provider-side capability being
   declared, not to an Airlock-managed one.
4. Record the re-scope explicitly in `0.5.9-verification.md` so the finding is
   closed by decision rather than by silence.

If provider-sandbox integration is wanted later, it belongs in its own release
with its own design — most naturally alongside the 0.6.x work, where MCP
capability tokens already raise similar boundary questions.

### Tests

None beyond existing coverage; no behavior changes. Documentation assertions
only.

---

## Sequencing

F-4 first — it is the live defect and F-1 Part B would depend on its reader.
F-3 next; it is small and its safety rests on a bit-identical-behavior test.
F-1 Part A after. F-2 is a documentation change that can land with any of them.

F-1 Part B and any real sandbox integration are explicitly **out of 0.5.9** and
recorded as such.

## Owner decisions required

1. Accept the F-2 re-scope (fix the claim, not the code)?
2. Accept F-3 as plumbing-only with enforcement default off?
3. Accept F-1 Part B deferral to 0.5.10?

If any of these should instead be built in full for 0.5.9, that changes the
milestone's size materially and should be said now rather than discovered at
closeout.
