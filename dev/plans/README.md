# dev/plans — orchestrated-work state spine

On-disk home for multi-agent orchestrated work. **Everything that must survive a
`/compact` or a new session lives here, not in chat.** Chat is throwaway working
memory; this tree is the source of truth.

## Current work

The active release is [0.5.9](0.5.9-plan.md); its live board is
[`runs/STATUS-0.5.9.md`](runs/STATUS-0.5.9.md) and its execution context is the
[steward handoff](prompts/0.5.9-MASTER-HANDOFF.md). Completed trains are
historical evidence catalogued in the [release archive](archive/README.md), not
active roadmap commitments.

## Layout

| Path | What it holds | Lifetime |
|------|---------------|----------|
| `dev/plans/<release>-plan.md` | The pack ladder + per-pack/per-AC scoreboard. Active only while its release is in flight. | Per release |
| `dev/plans/prompts/SLICE-TEMPLATE.md` | Version-neutral implementer prompt template (fill the `{{PLACEHOLDER}}`s per pack). | Stable |
| `dev/plans/prompts/MASTER-HANDOFF-TEMPLATE.md` | Per-release orchestrator kickoff template. | Stable |
| `dev/plans/prompts/<pack-id>.md` | The self-contained prompt actually handed to a pack's implementer. | Per pack |
| `dev/plans/runs/STATUS-<release>.md` | State board. Only the active release's board is live; closed boards are archive evidence. | Per release |
| `dev/plans/runs/<pack-id>-output.json` | Implementer closure artifact (schema in `.claude/agents/implementer.md` §6). | Per pack |
| `dev/plans/runs/<pack-id>-review-<ts>.md` | Promoted reviewer verdict (codex primary; see `dev/agent-harness-reference.md` §3). | Per pack |

## State is derived, not remembered

A pack's current state is the furthest state whose **on-disk witness** exists and
verifies — never what chat or `STATUS` claims (witnesses win on conflict). See the
runbook "State spine" section. The orchestrator re-derives position from these
artifacts on every resume; that is what makes the harness compaction-safe.

`PROGRESS.md` (repo root) is the narrative changelog of what landed. The live
state board is `runs/STATUS-<release>.md`. Do not duplicate live pack state into
`PROGRESS.md`.
