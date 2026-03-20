<!-- wake:codex:start -->
This repository uses wake for durable agent state shared across Claude and Codex.

- Start Codex sessions via `wake codex` so Wake renders startup context first.
- Read `.wake/projection.md` before substantial work and again before risky actions.
- For semantic events under Codex, prefix wake commands with `WAKE_PROVIDER=codex`.
- `wake codex` can keep `wake codex-bridge` active when a Codex JSON
  event source is configured.
- Fallback when not launched through `wake codex`:
  - start/resume task: `WAKE_PROVIDER=codex wake session-start`
    `&& wake render-wake-dir && wake render-projection`
  - log decisions/constraints/blockers as they happen
  - finish task: `WAKE_PROVIDER=codex wake session-end`
    `&& wake render-projection && wake render-wake-dir`

Wake files:
- Shared project briefing: `.wake/projection.md`
- Targeted reads: `.wake/constraints.md`, `.wake/decisions.md`,
  `.wake/blocked.md`, `.wake/rejected.md`
<!-- wake:codex:end -->
