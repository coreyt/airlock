# Slice 6 — HITL release review

**Decision recorded 2026-08-12:** the operator instructed implementation of the
plan. Adopt the recommended resolution below: include slices 10–90 and 120;
retain slices 100/110 and the LiteLLM/FathomDB upgrades as conditional; postpone
the broad documentation lifecycle execution and unrelated routine upgrades.
Slice 110 and the FathomDB 0.8.22 upgrade subsequently satisfied their
conditions; funded provider/embedding smokes were explicitly authorized and
passed. Release-closeout mechanics are now tracked separately as Slice 130.

A conditional item receives a delivery slice but may not start implementation
until its condition is met. This record consolidates Slices 0–5 and contains no
feature implementation.

Scales: understanding and risk use 1–4 (4 = highest); effort is engineering
size excluding review/funded-smoke wait. Recommendations reflect the currently
approved priority: FathomDB needs, providers, TUI, then operational backend.

| Candidate / slice | Drafts | Understood | Risk | Effort | Recommendation | Decision / condition |
| --- | --- | ---: | ---: | --- | --- | --- |
| Benchmark-safe logging profile / 10 | DFR/DAC-26 | 4 | 2 | S | Include | None; profile must keep SQL and Fathom raw content off. |
| `gpt-4o-mini` chat readiness / 20 | DFR/DAC-24 | 4 | 2 | S | Include | No implicit enable/fallback. |
| Embeddings and `text-embedding-3-small` / 30 | DFR/DAC-25 | 3 | 3 | M | Include | Ratify endpoint/capability contract before code. |
| Shared provider foundation / 40 | DFR/DAC-27 | 4 | 4 | M | Include | Must precede OpenRouter/DeepSeek. |
| OpenRouter / 50 | DFR/DAC-28 | 4 | 3 | M | Include | Curated aliases; operator-only routing/privacy policy. |
| DeepSeek / 60 | DFR/DAC-29 | 4 | 3 | M | Include | Stable API base; function tools only. |
| LiteLLM patch 1.96.2 | Slice 1 | 3 | 4 | M | Conditional | Characterize against 1.94.1 first; merge only if provider/embedding matrix is green. |
| FathomDB patch 0.8.22 | Slice 1 | 3 | 3 | S | Included, verified | Locked and installed; 110 DB-extra lifecycle/query/erasure/admin-operational tests passed. |
| TUI test lifecycle / 70 | DFR/DAC-30 | 4 | 2 | M | Include | Keep production-worker integration subset. |
| TUI routing/client diagnostics / 80 | DFR/DAC-31 | 3 | 3 | M | Include | Requires bounded source/staleness seam. |
| TUI QoS/exporter health / 90 | DFR/DAC-31 | 3 | 3 | M | Include | Instrument source before display. |
| Virtual-key management / 100 | DFR/DAC-32 | 2 | 4 | L | Postpone to 0.5.15 | Retain the draft package; complete design only after 0.6.0 keystore/identity reconciliation. |
| FathomDB operational reads / 110 | DFR/DAC-33 | 3 | 4 | L | Conditional | Single owner, source labels, fallback, and erasure honesty. |
| Documentation release-index contract / 120 | Slice 5 finding | 4 | 1 | XS | Include | Repair the stale 0.5.10 assertion while retaining active-release drift detection. |
| Documentation lifecycle execution | Slice 2 | 4 | 2 | M | Postpone | Keep proposal only in 0.5.14; approve a focused cleanup train later. |
| Non-LiteLLM/Fathom routine patch upgrades | Slice 1 | 3 | 2 | S–M | Postpone | Dependabot handles them unless advisory/feature need changes priority. |

## Adopted HITL resolution

1. **Include:** slices 10, 20, 30, 40, 50, 60, 70, 80, 90, and 120 in that order.
2. **Conditional:** LiteLLM patch upgrade only. Slice 110 and the FathomDB
   0.8.22 patch condition are complete. Slice 100 is postponed to 0.5.15.
3. **Postpone:** broad documentation lifecycle execution and unrelated routine
   library updates.

## Release gates that remain outside this decision

- PII egress remains observe-only until its existing owner DECIDE and evidence
  threshold are satisfied.
- The LiteLLM-child anonymous-memory owner is unresolved; FathomDB benchmark
  integration does not close it.
- Funded smokes require explicit operator authorization after no-credit tests;
  they do not run in CI and record no request/response content. The authorized
  0.5.14 embedding, OpenRouter, and DeepSeek smokes passed on 2026-08-12.

## HITL response form

Record one line per conditional item: `include`, `postpone`, or `retain
conditional`, plus any new release constraint. After the decision, change this
record's status and start the first approved feature slice using the required
TDD/review/status workflow.
