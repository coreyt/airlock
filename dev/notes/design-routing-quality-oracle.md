# Design (draft): a routing-quality oracle for the smart router

**Date:** 2026-08-28
**Status:** Draft design. **Not accepted scope.** Architecture review and HITL
approval are gates; §5 Stage 3 additionally requires explicit spend
authorization and a privacy decision.
**Scope:** `airlock/slow/` (offline), `airlock/measurement_report.py`,
`scripts/`, `dev/plans/runs/` evidence. Read-only over recorded logs. **No
change to the inference hot path, to `apply_routing`, or to any shipped routing
behavior.**
**Origin:** [design-switchyard-routing-evaluation.md](design-switchyard-routing-evaluation.md) §7 item 3.
**Depends on:** [design-conversation-text-extraction.md](design-conversation-text-extraction.md)
slice B — until extraction is fixed, recorded features are computed from a
truncated view of the conversation and every measurement inherits that bias.

---

## 1. Problem

Airlock ships a cost router that decides which model serves a request
(`model: "smart"`, cost tiers, session affinity) and **no way to tell whether it
decides well**. `tests/test_fast_router.py` has 98 tests; all of them assert
mechanism — that a directive is honoured, that a tier resolves, that a session
pins — and none assert quality. There is no baseline, no regression signal, and
no way to evaluate a proposed change to the classifier except argument.

That gap is why the Switchyard evaluation could not reach a recommendation on
the classifier itself. It demonstrated that `stage_router` *distinguishes* turns
Airlock's classifier scores identically; it could not show that it *decides
better*, because Airlock has nothing to measure "better" against. The same is
true of any tuning of `AIRLOCK_SMART_THRESHOLDS`, any new feature weight, and
any future third-party router. The oracle is the prerequisite, not the follow-up.

Airlock already has the shape of the answer elsewhere: the 0.5.12 PII-egress
work ran a pre-registered measurement window with named metrics, a value-free
report, and an explicit `human_decision_required: true` terminator
(`dev/plans/runs/0.5.12-pii-egress-measurement-protocol.md`). This design is
that pattern applied to routing.

## 2. What "quality" can honestly mean here

Routing quality is not one number, and the design must not pretend otherwise.
Three questions, in increasing order of cost and decreasing order of certainty:

| Question | Method | Costs money? | Claim strength |
|---|---|---|---|
| Q1 — Where would a candidate router disagree with the shipped one, and what would it cost? | Offline shadow scoring over recorded requests | No | **Descriptive only.** No quality claim. |
| Q2 — Did the tier we chose look sufficient in hindsight? | Retrospective outcome labels from later turns of the same session | No | Correlational, proxy-labelled |
| Q3 — Would the cheaper tier actually have completed this turn? | Counterfactual replay against both tiers, judged | **Yes** | Strongest, and the only one that answers the cost question directly |

Stage 1 delivers Q1 alone. That is deliberate: a disagreement-and-cost report is
genuinely useful (it sizes any proposed change before anyone commits to it), it
is cheap, and it makes no claim it cannot support. Q2 and Q3 are separate
stages with separate gates.

## 3. The labelling idea for Q2

The Switchyard investigation produced one reusable insight independent of the
library: a coding agent's own tool history says how the run is going — error
severity, churn without production, edits landing. Airlock's router discards
that as an *input*. The oracle can use it as a **label**.

For a recorded session, turn *N*'s routing decision is scored against what turns
*N+1…N+k* actually did:

- routed **efficient**, and the following turns show rising error severity,
  repeated identical tool calls, or an escalation back to a stronger model →
  candidate **under-route**;
- routed **capable**, and the following turns are settled production (edits and
  writes landing, tests passing, no errors) → candidate **over-route**;
- anything else → unlabelled. Most turns will be unlabelled, and that is fine.

These are proxy labels and must be reported as such — never as ground truth. A
run that fails for reasons unrelated to model capability will mislabel, which is
exactly why Stage 2 reports label *precision against a human-reviewed sample*
alongside every rate it publishes.

## 4. Draft requirements

- **DFR-48 — offline only.** The oracle runs in the slow path over recorded
  logs. It never registers a callback, never subscribes to the request path, and
  its absence or failure cannot affect an inference request. Architecture
  invariant 1 is satisfied structurally, not by measurement.
- **DFR-49 — replay is deterministic and versioned.** A candidate scorer is a
  pure function of a recorded request; the same corpus and the same scorer
  version produce a byte-identical report. Every report names the corpus window,
  record count, scorer version, and Airlock version, so two reports are
  comparable or are refused.
- **DFR-50 — value-free evidence.** Reports carry counts, rates, distributions,
  and cost deltas. Never prompt text, tool output, response text, client
  identity, or any PII. This is stricter than it sounds: recorded
  `messages_json` may contain unredacted tool-result content (see the extraction
  finding), so the oracle reads content only to compute features and never
  carries it into output.
- **DFR-51 — no automatic promotion.** The report terminates in
  `human_decision_required: true`. It does not write `airlock-knobs.json`, does
  not adjust `AIRLOCK_SMART_THRESHOLDS`, and does not change a tier map. A
  measurement that could silently retune the router is a control loop, not an
  oracle, and is out of scope.
- **DFR-52 — pre-registered metrics.** The decision metrics and their targets
  are written down *before* the measurement window opens, following the 0.5.12
  protocol. A metric invented after seeing the data is reported as exploratory
  and cannot support a promotion decision.
- **DFR-53 — a candidate scorer is pluggable and out-of-process.** Adding a
  scorer must not require touching `airlock/fast/router.py`. The shipped
  classifier is scorer zero; a Python tool-history scorer and, if it is ever
  released with a usable Python floor, a Switchyard-backed scorer are additional
  entries. None of them can reach the hot path from here.

### Stage 3 only (counterfactual replay)

- **DFR-54 — spend is authorized, bounded, and pre-declared.** Replay estimates
  its cost, refuses to start above an operator-set cap, and requires explicit
  authorization per run. No replay is triggered by a schedule.
- **DFR-55 — re-egress is a deliberate decision.** Replaying a recorded request
  re-sends its content to a provider, and that content may be unredacted (see
  the extraction finding). Replay runs against a synthetic or explicitly
  approved corpus only. Replaying production logs is a separate decision that
  this design does not make.

## 5. Stages

| Stage | Delivers | Gate |
|---|---|---|
| 0 | Corpus builder: derive a routing-decision dataset from recorded JSONL — request features, chosen tier, `airlock_routing` metadata, cost, session grouping | architecture review |
| 1 | **Q1 shadow report:** run candidate scorers over the corpus; report agreement matrix, tier distribution shift, and projected cost delta. No quality claim | architecture review |
| 2 | **Q2 outcome labels:** retrospective under/over-route rates, plus label precision against a human-reviewed sample | HITL — the sample review is the gate |
| 3 | **Q3 counterfactual replay** against a synthetic corpus, judged | HITL + spend authorization + privacy decision (DFR-55) |

Stage 1 is the one that pays for itself immediately and independently: it turns
"should we change the classifier?" from an argument into a number, for any
proposed change, including doing nothing.

## 6. Draft acceptance criteria

- **DAC-40** — the corpus builder produces a stable dataset from a fixture log
  directory; re-running yields an identical dataset; a malformed or truncated
  record is skipped and counted, never fatal.
- **DAC-41** — the shipped classifier scored against itself reports 100%
  agreement and zero cost delta. The trivial case must be exactly right before
  any comparison is believable.
- **DAC-42** — a deliberately different scorer produces an agreement matrix,
  tier-distribution shift, and a cost delta with a stated arithmetic basis
  (which price list, which token counts, what is excluded).
- **DAC-43** — no report field contains prompt text, tool output, response text,
  or client identity. Asserted by a test over the report schema, not by review.
- **DAC-44** — every report ends `human_decision_required: true` and writes no
  knob, threshold, or config file. Asserted.
- **DAC-45** — running the whole oracle over a corpus while the proxy serves
  traffic produces no measurable change in request latency; the oracle imports
  nothing from `airlock.fast` that could register a hook.

## 7. What this would have told us about Switchyard

Concretely: the evaluation measured that `stage_router` separates turns Airlock
scores identically, on three trajectories I wrote by hand. Stage 1 would replace
that with the real question — across a week of recorded dogfood traffic, on how
many turns do the two disagree, in which direction, and what would the cost
delta have been? Stage 2 would then say whether the disagreements were
under-routes or noise. That is the evidence needed to justify porting ~500 lines
of signal logic, or to drop the idea on measurement rather than on taste.

## 8. Open questions

1. **Corpus source.** JSONL, FathomDB projections, or both? FathomDB already
   projects `messages_json`, `model`, `cost`, `success`, and `airlock_client`,
   but it is optional in a default deployment, so JSONL is likely the floor and
   FathomDB an accelerator.
2. **Session reconstruction.** Q2 needs turns grouped into runs. `session_id`
   exists as a routing directive but is client-supplied and optional. What
   fraction of recorded traffic can actually be grouped, and is a heuristic
   fallback honest enough to label against?
3. **Cost basis.** Recorded `cost` reflects the model actually used. A
   counterfactual cost for the road not taken needs a price list and a token
   estimate; both are approximations and the report must say by how much.
4. **Relationship to `airlock/slow/tuner.py`.** The tuner already computes
   guardrail knobs from recorded observations and writes `airlock-knobs.json`.
   Whether the oracle shares its corpus plumbing while explicitly *not* sharing
   its write path (DFR-51) is a design decision for Stage 0.
5. **Retention.** A routing corpus is a derived dataset with its own lifetime.
   It inherits the JSONL retention obligation and must not become a quiet
   long-lived copy of conversation content.
